# Phase 6 — summary, AC37 evidence ledger and AC38 diff audit

Step P20 of `docs/campaigns/phase6/plan.md` (R10; AC37, AC38, AC39's summary half).
Authority: `docs/reviews/2026-10-16-full-adversarial-review.md` (rows `#1`–`#35`, cited `F1`–`F35`),
`docs/campaigns/phase6/spec.md`, `docs/campaigns/phase6/brief.md`.

- Pre-fix tree: `8010edc` (`docs(phase6): the review report, and the brief that answers it`).
- Evidence head: `98c303b` (P19, the last fix commit on `main`). Every commit hash below is a
  `main` commit and final.
- The gate half of AC39 (the full-branch review ending `APPROVE`) is P21's, not this document's.

## Result in one paragraph

36 entries: **34 findings closed** (F1–F33 and F35), each by a commit on `main` and a regression
test that passes at `98c303b`; **2 out of scope** (F34 and `claude:F8`), with the reason the brief and
the specification give. Every product-defect regression was re-run on `8010edc` by script and
**fails there** — none passes; the three test-defect findings (F27, F28, F32) were re-proven by
their stated mutations (fail under the mutation, pass restored). The diff audit finds **no added
`skip`/`xfail` and no deleted assertion**; it also finds **six existing tests outside the allowlist
(from P3, P10, P11, P13) whose assertions, expected values or setup values changed** (none loosened, every equality still exact) — recorded below as
an AC38 deviation for the gate, not hidden. Full suite: see [Suite counts](#suite-counts).

## Finding ledger (36 entries)

Evidence forms (AC37):
- **pre-fix** — product defect: the test fails on `8010edc` and passes at `98c303b`. Collected from
  the scripted re-run in [Evidence run](#evidence-run). Where the HEAD test file cannot be imported
  on `8010edc` because it names a constant the fix introduced, the run was repeated on `8010edc` with
  *only that name* appended (no behaviour), marked **pre-fix (name shim)**.
- **mutation** — test defect (F27, F28, F32): the stated mutation of the code under test makes the
  new or replacement assertion fail; restored, it passes.

Test ids are relative to `tests/`; a bare function name covers every parametrization.

| Finding | Severity | Closing commit (step) | Regression test id(s) | Evidence form | Pre-fix / mutation outcome |
|---|---|---|---|---|---|
| F1 compatibility route bypasses default-deny | major | `91f2274` (P2) | `test_twitch.py::test_ac1_an_ungranted_compatibility_send_is_refused_by_the_executor`, `::test_ac1_without_an_executor_the_compatibility_route_refuses`, `::test_ac2_the_compatibility_send_is_traced_as_an_executor_call` | pre-fix (name shim: `COMPATIBILITY_PRINCIPAL`) | 3 failed — a Helix POST is sent ungranted; no `action.started` trace |
| F2 agent executes actions outside the negotiated set / peer-chosen principal | major | `986bf92` (P3) | `test_proxy.py::test_p6_ac6_a_call_outside_the_negotiated_set_never_reaches_the_executor`, `::test_p6_ac7_the_frame_principal_is_compared_never_executed_under`, `::test_p6_ac7_the_default_pairing_principal_is_brain` | pre-fix | 3 failed — `'error' == 'refused'`, `'success' == 'refused'`; the `principal` setting is unknown (¹) |
| F3 retained call id returns another call's observation before authorization | minor | `ce4f86b` (P1) | `test_actions.py::test_ac3_a_reused_call_id_naming_another_call_is_refused` (×5 components), `::test_ac3_a_reuse_whose_arguments_only_share_a_repr_is_refused`, `::test_ac3_an_identical_reuse_the_policy_now_refuses_is_refused` | pre-fix (name shim: `_identity`) | 7 failed — `'success' == 'refused'` |
| F4 configured credential echoed when used as a mapping key | minor | `35431e1` (P5) | `test_loader.py::test_ac10_a_hook_diagnostic_never_names_another_modules_credential`, `::test_ac10_a_schema_diagnostic_masks_a_credential_used_as_a_key`, `::test_ac10_streamerbot_settings_never_echo_the_password_used_as_a_key`, `test_main.py::test_ac10_check_config_never_echoes_a_credential_used_as_a_key`, `::test_ac10_a_refused_startup_keeps_the_credential_out_of_supervision`, `test_redaction_surfaces.py::test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive` | pre-fix | 26 failed, 3 passed — the 3 passing cases are the `settings` (top-level key) placement, which the old hook guard already masked (²) |
| F5 peer error code can disclose the pairing token in diagnostics | minor | `986bf92` (P3) | `test_proxy.py::test_p6_ac8_peer_strings_never_enter_diagnostics_or_supervision_records` (token, 4 KiB) | pre-fix | 2 failed — `'pairing-secret-token' not in` the supervision records |
| F6 `!forgetme` leaves the brain's conversation memory intact | major | `e0c1d0b` (P7); test synchronisation `321409a` (P8F1) | `test_brain.py::test_ac13_forgetme_empties_the_brain_memory_of_that_key_only`, `::test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it` | pre-fix | 2 failed — recall still holds the erased exchange; `ConversationMemory` has no `epoch` (¹) |
| F7 concurrent duplicate `call_id` reaches the provider twice | major | `ce4f86b` (P1) | `test_actions.py::test_ac4_concurrent_identical_calls_reach_the_provider_once`, `::test_ac4_a_concurrent_reuse_naming_another_call_is_refused`, `::test_ac4_a_cancelled_first_caller_still_settles_its_waiter` | pre-fix (name shim: `_identity`) | 3 failed — provider count `2 == 1`; `ContractError … refusing to overwrite` |
| F8 raising supervision call kills the bridge owner task | major | `1d16d57` (P8) | `test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive`, `::test_ac15_phase6_a_permanently_raising_trace_keeps_the_backoff` | pre-fix (name shim: `COMPATIBILITY_PRINCIPAL`) | 2 failed — the `_own()` task is finished; timeout |
| F9 refused token (401/403) retried forever, never `degraded` | major | `2ac5613` (P9) | `test_twitch.py::test_ac16_an_authentication_refusal_of_the_chat_subscription_ends_recovery` (401, 403) | pre-fix (name shim: `COMPATIBILITY_PRINCIPAL`) | 2 failed — extra recovery sockets opened |
| F10 no EventSub keepalive watchdog | major | `2ac5613` (P9) | `test_twitch.py::test_ac17_a_socket_silent_past_its_keepalive_is_recovered` | pre-fix (name shim: `COMPATIBILITY_PRINCIPAL`, `KEEPALIVE_MARGIN_SECONDS`) | 1 failed — no recovery within the turn budget |
| F11 first YouTube page after a resolution re-ingests the backlog | major | `0c3e0f6` (P10) | `test_youtube.py::test_ac18_the_first_page_of_a_fresh_resolution_admits_nothing`, `::test_ac18_the_first_page_after_a_re_lookup_admits_nothing` (403, 404) | pre-fix | 3 failed — the backlog (`old-1…old-3`) is admitted |
| F12 no byte bound on model responses before JSON decoding | minor | `374d88d` (P11) | `test_brain.py::test_ac19_a_body_one_byte_over_the_bound_fails_the_run_undecoded`, `::test_ac19_a_long_body_is_read_only_to_one_byte_past_the_bound`, `test_model_adapter.py::test_ac19_a_probe_answer_over_the_bound_is_never_decoded` | pre-fix | 3 failed — 0 bytes read through the stream (`.json()`); probe DID NOT RAISE |
| F13 no admission cap on unauthenticated proxy connections | major | `953bd96` (P4) | `test_proxy.py::test_p6_ac20_a_third_unpaired_connection_beyond_the_cap_of_2_is_refused` | pre-fix | 1 failed — `max_unpaired_connections` refused as unknown (¹) |
| F14 agent call cache does not bound running executions / waiters | minor | `986bf92` (P3) | `test_proxy.py::test_p6_ac9_running_executions_and_duplicate_waiters_are_bounded` | pre-fix | 1 failed — the two bounds refused as unknown settings (¹) |
| F15 guide gives base URLs, adapter needs the full Chat Completions URL | major | `cf24dde` (P18) | `test_installation_doc.py::test_the_guides_endpoint_values_are_complete_chat_completions_urls`, `::test_the_guides_profile_posts_to_the_guides_endpoint` | pre-fix | 4 failed — `https://api.openai.com/v1`, `http://localhost:11434/v1` |
| F16 README config-UI command cannot run | minor | `cf24dde` (P18) | `test_installation_doc.py::test_the_readme_config_ui_command_runs` | pre-fix | 1 failed — `No module named twitch-ia-compagnon-config-ui` |
| F17 guide names the overlay `config.local.yaml` | minor | `cf24dde` (P18) | `test_installation_doc.py::test_the_documents_name_the_overlay_the_code_derives` (both guides, README) | pre-fix | 3 failed — `['config.local.yaml']` |
| F18 doc test greps text instead of resolving commands (allowlisted replacement) | nit | `cf24dde` (P18) | `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` (+ `::test_the_pre_fix_readme_line_does_not_resolve`) | pre-fix | 1 failed, 2 passed — `[README.md]` fails (`twitch-ia-compagnon-config-ui: no such module`); the two guide cases pass, the guides' commands already resolved (²) |
| F19 silent pre-`hello` peer held forever | minor | `953bd96` (P4) | `test_proxy.py::test_p6_ac20_a_silent_connection_is_closed_at_the_hello_deadline_and_not_one_tick_before`, `::test_p6_ac20_a_peer_that_stops_reading_cannot_hold_its_slot_past_the_hello_deadline` | pre-fix | 2 failed — `hello_deadline_seconds` refused as unknown (¹) |
| F20 deeply nested tool arguments raise `RecursionError` | minor | `374d88d` (P11) | `test_brain.py::test_ac21_arguments_nested_10000_levels_are_malformed_and_the_run_goes_on`, `test_model_adapter.py::test_ac21_probe_arguments_nested_10000_levels_raise_brain_module_error` | pre-fix | 2 failed — run ends `run_failed`; probe leaks `RecursionError` |
| F21 viewer memory evicts before a write that then fails | minor | `f74067e` (P14) | `test_viewer_memory.py::test_ac25_a_write_failing_for_space_evicts_nothing`, `::test_ac25_a_rename_failing_after_evictions_publishes_their_exact_count`, `::test_ac25_a_use_whose_rename_fails_carries_its_evictions` | pre-fix | 3 failed — a record is deleted before the failed write; raw `OSError: [Errno 28]` |
| F22 leaked `.viewer-memory-*.tmp` never removed or counted | minor | `f74067e` (P14) | `test_viewer_memory.py::test_ac26_a_leftover_temporary_file_is_counted_then_reclaimed`, `::test_ac26_a_write_cut_short_counts_its_kept_temporary_at_its_real_size`, `::test_ac26_a_scan_reclaims_a_temporary_file_and_never_reads_it` | pre-fix | 3 failed — the temporary is neither counted nor removed; no `temporaries` (¹) |
| F23 admission's ready deque keeps removed sessions | minor | `63a81ac` (P12) | `test_admission.py::test_expired_and_evicted_sessions_never_accumulate_in_the_ready_structure` | pre-fix | 1 failed — `assert 5 <= 4` |
| F24 Twitch `chat.write` has no 500-character bound | minor | `2ac5613` (P9) | `test_twitch.py::test_ac23_chat_write_refuses_text_over_500_characters_before_any_request`, `::test_ac23_the_compatibility_route_reaches_the_500_character_check` | pre-fix (name shim: `COMPATIBILITY_PRINCIPAL`) | 2 failed — `'success' == 'refused'`; the POST is sent |
| F25 Kick dedup window shorter than the accepted signature span | minor | `2f2c7cc` (P13) | `test_kick.py::test_ac24_the_dedup_window_is_never_shorter_than_twice_the_tolerance` | pre-fix | 1 failed — `window_seconds: 300` accepted (0 diagnostics) |
| F26 misspelled profile keys silently accepted | minor | `7fb79ee` (P6) | `test_loader.py::test_f26_an_undeclared_setting_is_refused_by_its_path_at_every_depth`, `::test_f26_a_credential_used_as_an_unknown_key_is_masked`, `test_main.py::test_ac12_check_config_refuses_an_unknown_key_naming_its_path`, `::test_ac12_main_check_config_exits_non_zero_on_an_unknown_key`, `::test_ac12_a_credential_used_as_an_unknown_key_is_redacted` | pre-fix | 10 failed — `check_config` exits 0 with no diagnostic |
| F27 history token budget / 50 % reservation / order unasserted | minor | `98c303b` (P19) | `test_brain.py::test_history_beyond_the_token_budget_is_dropped`, `::test_history_leaves_half_the_token_budget_for_the_output`, `::test_history_reaches_the_prompt_in_chronological_order` | mutation (AC34) | M1 remove the budget: 2 failed, 1 passed · M2 reservation 0.5→1.0: 2 failed, 1 passed · M3 drop `kept.reverse()`: 3 failed · restored: 3 passed |
| F28 bridge AC16 test passes vacuously alone (allowlisted replacement) | nit | `98c303b` (P19) | `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success` | mutation (AC35) | bridge returns `success` for an acknowledged run: 1 failed (`ActionObservation(status='success' …)`) · restored: 1 passed |
| F29 `cancel_grace_seconds` accepts NaN/inf | nit | `ce4f86b` (P1) | `test_actions.py::test_ac5_a_non_finite_cancel_grace_is_refused` (nan, inf, -inf) | pre-fix (name shim: `_identity`) | 3 failed — nan and inf DID NOT RAISE; -inf was already refused on `8010edc` as negative and fails only on the "finite" wording (²) |
| F30 `audio_output.stop` always sleeps the full grace | nit | `836e734` (P16) | `test_audio_output.py::test_stop_returns_as_soon_as_the_player_exits_within_the_grace` | pre-fix | 1 failed — "stop waited out the grace after the player had exited" |
| F31 `$`-anchored memory-file scan accepts a trailing newline | nit | `f74067e` (P14) | `test_viewer_memory.py::test_ac27_a_memory_name_with_a_trailing_newline_is_foreign` | pre-fix | 1 failed — `<64 hex>.json\n` is read and deleted |
| F32 tautological `EventBus(...) is not None` (allowlisted replacement) | nit | `9a74bd1` (P15) | `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting` (21 cases); AC36 `test_bus.py::test_no_constructor_result_is_asserted_merely_not_none` | mutation (AC37) | `EventBus.__init__` stores the default instead of `history_max_bytes`: 21 failed · restored: 21 passed |
| F33 uncopyable payload makes `publish` raise a raw `TypeError` | nit | `9a74bd1` (P15) | `test_bus.py::test_retention_copy_failure_is_a_publication_error_with_history_record` | pre-fix | 1 failed — `TypeError: cannot copy this value` escapes `publish` |
| F34 stalled trace subscriber defeats an action deadline (`codex:F6`) | minor | — **out of scope** | — | — | Challenged in cross-review (report, "Ajusté en cross-review"): the mechanism is real but the only `action.*` subscriber is the audit, synchronous and non-blocking; the scenario needs an out-of-tree subscriber. Excluded by the brief ("Out of scope") and the spec ("Finding accounting"); kept as a robustness note. Not fixed. |
| F35 config-UI Check waits forever on an abandoned validator | minor | `1f14dcd` (P17) | `test_config_ui.py::test_p17_ac30_a_validator_that_never_returns_releases_the_check_by_the_deadline`, `::test_p17_ac30_stalled_cleanup_after_a_validator_timeout_gets_only_the_residual`, `::test_p17_ac30_cleanup_stages_after_an_early_validation_share_the_remaining_budget` | pre-fix | 3 failed — no `CHECK_RELEASE_SECONDS` / `_run_check_bounded` (¹); P17 also proved the shared deadline by mutation (a fresh 1 s budget per stage → cases a and b fail) |
| `claude:F8` Streamer.bot backoff resets to `min_delay` after a loss | — | — **out of scope** | — | — | Challenged in cross-review and set aside: the reset is documented behaviour (`modules/streamerbot/__init__.py:2328`), a flapping instance is reconnected at the minimum as specified. Not a table row; excluded by the brief and the spec. P8 left the backoff in `_own` unchanged. Not fixed, by decision. |

Notes on the evidence:
1. **Fails at the new seam, not only on behaviour.** On `8010edc` these tests fail before reaching
   the defect because the setting or attribute the test drives did not exist (`principal`,
   `max_unpaired_connections`, `hello_deadline_seconds`, `max_running_calls`/`max_duplicate_waiters`,
   `ConversationMemory.epoch`, `MemoryStore.temporaries`, `CHECK_RELEASE_SECONDS`/`_run_check_bounded`).
   Each finding listed with (¹) also has a sibling test or parametrization in the same row that
   fails on behaviour (F2: `test_p6_ac7_the_default_pairing_principal_is_brain` executes as `success`;
   F6: `test_ac13` recall still holds the exchange; F22: the leftover temporary is not counted), or
   the step recorded a behavioural mutation of the new code (F13/F19: five mutations in P4; F14:
   bound mutations in P3; F35: per-stage budget mutation in P17). The gate (P21) re-runs the report's
   reproductions for the behavioural half.
2. **Guard parametrizations.** F4's `settings` placement, F18's two guide cases and F29's `-inf`
   check behaviour `8010edc` already had; every other parametrization of the same test fails there.

## Evidence run

Scripted (not by hand). Trees are `git archive` extractions in a scratch directory, never the
working tree; the project virtualenv runs every test (`core` resolves to the extracted tree — checked).

- **HEAD** (`98c303b`): every test id above, one run → **131 passed**, 0 failed.
- **8010edc**: the `8010edc` tree with `98c303b`'s `tests/` copied in; each finding's ids run
  separately. Raw lines:

```
F1  1 error  (collection: COMPATIBILITY_PRINCIPAL)   -> name shim: 3 failed
F2  3 failed            F13 1 failed            F22 3 failed
F3  1 error  (collection: _identity)                 -> name shim: 7 failed
F4  26 failed, 3 passed F14 1 failed            F23 1 failed
F5  2 failed            F15 4 failed            F24 1 error -> name shim: 2 failed
F6  2 failed            F16 1 failed            F25 1 failed
F7  1 error  (collection: _identity)                 -> name shim: 3 failed
F8  1 error  (collection: COMPATIBILITY_PRINCIPAL)   -> name shim: 2 failed
F9  1 error  -> name shim: 2 failed                  F26 10 failed
F10 1 error  -> name shim (+KEEPALIVE_MARGIN_SECONDS): 1 failed
F11 3 failed            F18 1 failed, 2 passed  F29 1 error -> name shim: 3 failed
F12 3 failed            F19 2 failed            F30 1 failed
F17 3 failed            F20 2 failed            F31 1 failed
F21 3 failed            F33 1 failed            F35 3 failed
```

  The name shim appends to the `8010edc` tree only `COMPATIBILITY_PRINCIPAL = "channel.chat.send"`
  and `KEEPALIVE_MARGIN_SECONDS = 5.0` (`modules/twitch/__init__.py`, HEAD's values) and a
  `_identity` stub that raises (`core/actions.py`; used only by a test not listed above). No
  behaviour is added; every shim failure above is an assertion on behaviour (POST sent, `success`
  instead of `refused`, provider count 2, owner task finished, extra sockets, no recovery, DID NOT
  RAISE).
- **Fix reverted** (cross-check): `98c303b`'s tree with only the step's product files restored from
  the step's parent commit. Same outcome per finding as `8010edc` (F8, F9, F10, F24 fail on behaviour
  there without any shim); F1, F3, F7, F29 error at collection there too, and are covered by the name
  shim above.
- **Mutations** (F27, F28, F32): applied to the HEAD extraction one at a time, run, restored
  (byte-identical), run again — outcomes in the ledger. On `8010edc` the F28 test file does not import
  (twitch name), and the F32 replacement fails (no `history_limits`), which is why the mutation, not
  the pre-fix run, is these findings' evidence (AC37).

## AC38 — diff audit (`git diff 8010edc..HEAD -- tests`)

19 files, +4,723 / −65 lines. Method: every `+` line searched for `skip`, `xfail`, `importorskip`;
every `-` line read (all 65 shown by `git diff -U0`), classified, and each touched existing test
compared before/after.

**No added `skip` or `xfail`.** Zero `+` lines match.

**No deleted assertion, no deleted test.** The 65 removed lines are imports, `activate_with(...)` /
`start_harness(...)` setup lines, closing lines of extended docstrings, fixture schemas, the
module-level `RUN_OBSERVATIONS` list and its `.append` (F28), and the assertion lines of the
replacements below. `test_youtube.py::test_the_default_policy_admits_a_mention_of_the_companion`
appears as removed-then-added: it is the same test, its setup prefixed with the priming page, both
assertions unchanged.

**Allowlisted tests (9), each with a docstring citing its finding and requirement:**

| Test | Change | Docstring |
|---|---|---|
| `test_twitch.py::test_chat_send_uses_configured_identity_and_preserves_event` | fixture only (`compatibility_context()`), assertions unchanged | F1/R1 |
| `test_twitch.py::test_send_failure_continues_chain_and_next_send_succeeds` | fixture only (`compatibility_context(audit_bus=EventBus())`: the executor's traces go to their own bus so the exact-history assertion keeps checking the route's events), assertions unchanged | F1/R1 |
| `test_twitch.py::test_pending_send_releases_response_on_timeout_cancellation_and_close` | same fixture as above, assertions unchanged | F1/R1 |
| `test_twitch.py::test_notification_mapping_dedup_and_retry_reconnect` | fixture only, assertions unchanged | F1/R1 |
| `test_twitch.py::test_lost_sent_trace_neither_retries_nor_repeats_the_send` | docstring only (its existing grant already covered the principal) | F1/R1 |
| `test_twitch.py::test_confirmed_outcome_never_waits_for_the_sent_trace` | docstring only | F1/R1 |
| `test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` | **assertions replaced**: the grep becomes command resolution over both guides and the README; still requires `core.main`, `core.config_ui`, `demo` and the guides' `--config mon-compagnon.yaml --check-config` | F18, R8 |
| `test_automation_bridge.py::test_ac16_no_run_observation_is_a_success` | **assertions replaced**: drives two runs itself, ≥ 1 observation, none `success` | F28, R9 |
| `test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting` | **one line replaced**: `is not None` → `.history_limits == _VALID_LIMITS`; the parametrized rejection assertions unchanged | F32, R9 |

The three replacements are established at least as strong by AC37 (ledger rows F18 — fails on
`8010edc`; F28, F32 — fail under their mutation). Gate observation on F18: the old test also matched
the guides' exact strings `python -m core.main --config mon-compagnon.yaml` and
`python -m core.config_ui --config mon-compagnon.yaml`; the replacement requires those modules to be
printed and to resolve, and keeps the exact `--check-config` string, but no longer pins the
`--config mon-compagnon.yaml` argument on those two lines. It is an allowlisted replacement and meets
AC37; the narrowing is recorded for the gate to judge.

**Outside the allowlist — AC38 deviation, recorded for the gate (P21).** AC38 says the only change
to an existing test's assertions is an allowlisted replacement. The audit finds existing tests
outside the allowlist that were changed. None is loosened (every equality stays exact, no assertion
removed), all but two carry a comment or docstring citing their finding, and P3, P10, P11 and P13
reported the change in their step output; but they are not on the allowlist:

| Test | Step | Change | Loosened? |
|---|---|---|---|
| `test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant` | P11 (F12) | exact property-set equality extended with `max_response_bytes`; an assertion on its type and minimum added; docstring cites R6/F12 | no — still exact |
| `test_proxy.py::test_agent_link_manifest_declares_the_input_role_no_event_and_no_action`, `::test_agent_link_validate_settings_accepts_loopback_ws_and_wss_anywhere`, `::test_agent_link_validate_settings_refuses_bad_entries_and_non_finite_bounds` | P3 (F2, F14) | exact sets / inputs extended with the three new optional settings (`principal`, `max_running_calls`, `max_duplicate_waiters`); assertions added; a comment cites R2 in the manifest test only — the two `validate_settings` tests carry no citation |  no — additions only |
| `test_youtube.py::test_ac39_three_platforms_on_one_runtime` | P10 (F11) | `api.counts["list"] == 1` → advance one poll, `== 2` (the message now follows the priming page); comment cites R5/F11 | no — still an exact count |
| `test_kick.py::test_the_dedup_window_is_bounded_by_its_settings` | P13 (F25) | setup only: window 300 → 600 (300 is now refused), clock advance 301 → 601; assertions unchanged; docstring cites R6/F25/AC24 | no |

Setup-only adaptations outside the allowlist, assertions unchanged, each docstring citing its
finding: `test_twitch.py::test_invalid_send_is_a_failed_attempt_without_request` (grant fixture,
F1/R1 — flagged by P2); the YouTube tests that now script an empty priming page first
(`test_ac38_each_author_flag_satisfies_its_audience_rule`,
`test_ac38_each_notice_type_maps_to_its_kind_and_is_admitted_by_kind`,
`test_an_unlisted_notice_and_an_unmapped_type_are_ignored_and_counted`,
`test_ac32_an_author_id_with_a_colon_is_refused_and_counted`,
`test_a_relisted_message_is_ingested_once_and_counted`,
`test_the_default_policy_admits_a_mention_of_the_companion`,
`test_owning_by_default_the_pollers_start_as_before` — R5/F11, flagged by P10); fixture schemas made
to declare the keys they carry, after P6 closed undeclared settings
(`tests/fixtures/modules/fakeeffects/module.yaml`, `test_config_ui.py::_publisher_base`, fixture
schemas in `test_loader.py` and `test_main.py` — flagged by P6); `test_brain.py::activate_memory_platform`
gains an optional `session` (P7).

These deviations cannot be resolved inside P20 (its only file is this summary): either the gate
accepts them as the finding's own declaration changes, or the allowlist in `spec.md` is amended to
name them. Nothing in them weakens the suite.

`tests/test_phase5_docs_status.py` is unchanged since `8010edc` (`git diff --quiet 8010edc HEAD --
tests/test_phase5_docs_status.py`) and passes.

## Suite counts

- Full suite at `98c303b`: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` →
  **3849 passed, 26 skipped in 251.38s**, 0 failed. Phase floor: 3,124 passed / 18 skipped.
- `tests/test_phase5_docs_status.py` alone → **27 passed in 0.33s**.

## Notes carried from the steps (not closed here; for the gate)

Sibling paths the steps reported instead of widening their scope:
- P3: the agent's executor still traces peer-supplied call fields (`run_id`, `conversation_id`,
  `source_event_id`, `message_id`) in its `action.*` records — same class as F5, in the core
  executor's trace.
- P7: a run admitted before `!forgetme` still calls viewer_memory's `memory.record` afterwards (brain
  `_record_memory`), which can re-create the erased viewer_memory file with that run's text. The
  brain's own conversation memory is protected by the epoch (F6, AC14); the viewer_memory file is not.
- P8: `_trace_decision` awaits `supervision.emit` unguarded on the event-consumer path (not the owner
  task); the `degraded` report in `_own`'s cancellation `finally` is unguarded on purpose.
- P11: a model response with no byte stream falls back to `response.json()` (test doubles only; a
  real client always has a stream). The probe reports an oversized answer as `no_tool_call`.
- P14: the offline CLI (`modules/viewer_memory/__main__.py`) does not reclaim `.viewer-memory-*.tmp`.
- P9: a 401/403 at startup still fails `start_inputs` (pinned by a non-allowlisted test); AC16 is
  enforced on the recovery path.

## Fix round and closing gate (added after the fourth pass)

The ledger above is P20's, written before the closing gate ran. The gate then refused **three times**,
each time on a narrower ground, and its fourth pass approved.

**First pass — `docs/campaigns/phase6/full-branch-review.md`.** 29 findings closed; **F2, F3, F4, F5 and
F6 open**, each with an executed probe, plus three defects in the phase's own evidence (N1–N3). Five fix
steps answered them:

| Step | Commit | What it closed |
|---|---|---|
| P20F1 | `67906ce` | **F2, F3** — the agent's retained call cache checks the call identity (action, principal, destination, arguments) *before* serving a retained observation; an altered retry is refused, an exact retry still returns it. Probe A: `refused/unknown_action`, `refused/not_authorized`, `refused/call_id_conflict`, `executor_calls=1`; exact retry `success, retained_equal=true`. |
| P20F2 | `6f72939` | **F4, N1** — one redacting constructor for hook diagnostics, and the structural test now proves the invariant: all four probe-D fixtures (raw f-string, `.format`, concatenation, raw f-string behind an unrelated redactor call) fail, the positive control passes; the offline `viewer_memory.__main__` consumer prints nothing unredacted (probe C: `exit=2`, empty stdout). |
| P20F3 | `63958be` | **F5** — no peer-supplied string reaches the executor's supervision payloads; probe B: `leaking_records=0`, `leaking_diagnostics=0`, the call still executes and returns its result, the local fields stay in the trace. |
| P20F4 | `6addd6d` | **F6** — the persistent store refuses a record admitted before its viewer's erasure; probe E: `viewer_memory_record_exists=false`, `erased_text_in_any_store_file=false`, `store_refusals=2`, a later and an other-key write still succeed. |
| P20F5 | `8f0bd26` | **N2, N3** — the F18 replacement asserts the guide's `--config mon-compagnon.yaml` again (changing it to `wrong.yaml` now fails), and `scaffolding/plan-amendments.md` records the five out-of-allowlist tests as explicit extensions, one entry each. |

**Second pass — `full-branch-review-2.md`.** All 34 in-scope findings and N1–N3 **closed**; probes A–E
pass; the assertion-AST comparison over 2,184 pre-existing test functions finds no deletion and no
loosening. One item remained: **literal AC2 named an `action.requested` trace the kernel does not
define**.

**Amendment A1 — `scaffolding/plan-amendments.md`.** AC2 now names `action.started`
(`core/contracts.py:149`), the single start trace the executor publishes. No code and no test changed:
the criterion's own closing clause ("the same traces an executor call produces") already settled it, and
adding a second event to satisfy a typo would add a redundant trace to the evidence trail R8 exists to
protect.

**Third pass — `full-branch-review-3.md`.** Everything stayed closed and the amended AC2 behaviour passed;
it refused on a documentary point: two **prescriptive** clauses (the plan's AC2 line and the P2 step
spec) still named the old token. Both are aligned in `12f7329`, and A1's absence check now scopes itself
to prescriptive text, because the amendment record and the gate reports must quote the old name to
document the change.

**Fourth pass — `full-branch-review-4.md`: APPROVE.** A1 PASS, F1–F33 and F35 CLOSED, N1–N3 CLOSED,
F34 and `claude:F8` OUT-OF-SCOPE, probes A–E pass, R1–R10 pass within the stated verification limits.

**Corrections to the "known limits" above, where the fix round changed them.** The offline
`modules/viewer_memory/__main__` consumer no longer prints an unredacted hook diagnostic (P20F2), and the
persistent store no longer recreates an erased viewer's record (P20F4). The other limits stand as
written.

**Final state.** `main` at the addendum's commit, suite **3,901 passed, 26 skipped**, closing report
`docs/campaigns/phase6/full-branch-review-4.md`. The bridge remains **NOT delivered**: no real-instance
trial record exists, and this document does not create one.
