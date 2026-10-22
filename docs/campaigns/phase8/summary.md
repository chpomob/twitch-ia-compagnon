# Phase 8 — summary: triggers a module can declare

Step P16 of `docs/campaigns/phase8/plan.md` (R9; AC32's evidence half, AC33).
Authority: `docs/campaigns/phase8/spec.md` (R1–R9, AC1–AC40), `docs/campaigns/phase8/brief.md`
and its binding decisions, `docs/design-v2.md`.

- Pre-phase tree: `7782e5f` (`docs(phase8): the approved specification, …`).
- Evidence head: the P16 commit, on top of `0633b8e` (P15e). A commit cannot cite its own hash, so
  P16 is named, not hashed.
- The real-instance trial is a manual procedure (`docs/triggers.md`, "Manual real-instance trial");
  its record is [`trial-record.md`](trial-record.md), status `not run`.

## Result in one paragraph

A module now declares its trigger types in its manifest and the kernel evaluates them: a declarative
`match` tree, an optional single probability parameter, and at most one bounded, fail-closed hook
that runs only on the declaring module's own events. A declared type only narrows candidacy and
grants nothing: admission, budgets and default-deny authorization decide everything after it. The
vocabulary grew by appending `reward`, `manual`, `speech` and `timer` after `cue`. Four families use
it: Twitch channel points (`reward`), the operator's button (`operator` input, three local
surfaces, one press command), the streamer's voice (`audio_input` as an input, `wake_word` hooked
type over a configured detector), and a timed random trigger (`timer` input, `interval` type). No
runtime dependency was added and no shipped `*.example` profile was edited. Every family is proven
against fakes only, and the whole suite now runs under one isolation guard that makes socket
creation and process spawning raise (AC33).

## Requirement ledger (R1–R9)

Commits are the plan steps: `5a78832` P1 (baseline, guard), `2bfe69e` P2 (vocabulary), `3d9b726`
P3 (contract shapes), `73e972f` P4 (loader), `1721a93` P5 (registry), `a5a5acd` P6 (declared-rule
evaluation), `6349fe5` P7 (hooked types), `f009530` P8 (channel points), `e2bee50` P9 (`operator`),
`3c0881a` P10 (press command), `4219e19` P11 (`timer`), `e643d01` P12 (`audio_input` input),
`bfce006` P13 (listen pipeline), `6668568` P14 (listen lifecycle), `7093597` P15 (guide, README),
`4465d74`/`709662f`/`4b549f5`/`8724a2d`/`0633b8e` P15a–P15e (AC33 conversions). The commit named
against a test is the one that introduced it (`git log -S "def <name>("`). A bare function name
covers every parametrization.

| Req. | Commit(s) | Test id(s) | ACs |
|---|---|---|---|
| R1 a module declares trigger types, accepted at load | `3d9b726`, `73e972f`, `1721a93` | `tests/test_declared_triggers.py::test_p3_ac2_structural_rows_are_refused_by_sub_path` (`3d9b726`); `tests/test_declared_triggers.py::test_p4_ac1_the_badge_tier_manifest_loads_with_its_declared_field` (`73e972f`); `tests/test_declared_triggers.py::test_p5_ac1_the_registry_accepts_the_badge_tier_declaration` (`1721a93`) | AC1–AC3 |
| R2 candidacy only, narrows only | `a5a5acd`, `1721a93`, `6349fe5` | `tests/test_declared_triggers.py::test_p6_ac4_badge_tier_decides_by_the_payload_field`, `tests/test_declared_triggers.py::test_p6_ac8_matches_is_a_case_insensitive_wildcard` (`a5a5acd`); `tests/test_declared_triggers.py::test_p5_ac6_an_override_naming_another_inputs_type_is_refused` (`1721a93`); `tests/test_declared_triggers.py::test_p7_ac9_an_accepted_declared_decision_grants_nothing` (`6349fe5`) | AC4–AC10, AC35, AC36 |
| R3 a hooked type is bounded and fail-closed | `6349fe5` | `tests/test_declared_triggers.py::test_p7_ac12_a_hook_that_never_returns_times_out_on_the_bound`, `tests/test_declared_triggers.py::test_p7_ac14_only_the_declaring_module_supplies_its_hook`, `tests/test_declared_triggers.py::test_p7_ac34_a_failed_hook_vetoes_whatever_the_combination` (`6349fe5`) | AC11–AC14, AC34 |
| R4 the vocabulary grows by appending | `2bfe69e`, `5a78832` | `tests/test_declared_triggers.py::test_ac15_the_four_kinds_are_appended_after_the_phase7_ten`, `tests/test_declared_triggers.py::test_ac16_a_brain_route_matching_manual_validates_at_startup` (`2bfe69e`); `tests/test_declared_triggers.py::test_ac16_shipped_profiles_decide_as_in_phase7` (`5a78832`) | AC15, AC16 |
| R5 channel points are a first-class trigger | `f009530` | `tests/test_twitch.py::test_ac17_reward_subscription_is_requested_only_when_listed`, `tests/test_twitch.py::test_ac18_a_refused_reward_subscription_degrades_reward_and_chat_continues`, `tests/test_twitch.py::test_ac19_a_redemption_normalizes_to_one_reward_event`, `tests/test_twitch.py::test_ac20_the_reward_type_selects_on_reward_id_and_cost_bounds` (`f009530`) | AC17–AC20 |
| R6 the operator button, three surfaces, one event | `e2bee50`, `3c0881a` | `tests/test_operator.py::test_ac21_no_listener_before_start_inputs_and_a_press_from_page_and_overlay`, `tests/test_operator.py::test_ac22_every_refusal_gives_no_event_and_its_count_and_echoes_nothing`, `tests/test_operator.py::test_ac24_a_button_policy_accepts_hype_and_rejects_other` (`e2bee50`); `tests/test_operator.py::test_ac40_one_post_to_the_press_endpoint_naming_hype_hotkey_and_the_token` (`3c0881a`) | AC21–AC24, AC40 |
| R7 the streamer's voice is an input; the wake word is hooked | `e643d01`, `bfce006`, `6668568` | `tests/test_audio_input.py::test_p12_ac25_without_listening_nothing_is_invoked_over_60_seconds`, `tests/test_audio_input.py::test_p12_ac27_a_never_returning_hook_times_out_after_2_seconds` (`e643d01`); `tests/test_audio_input.py::test_p13_ac26_the_candidate_is_decided_before_any_audio_then_one_speech_event`, `tests/test_audio_input.py::test_p13_ac28_five_accepted_candidates_store_and_write_no_audio` (`bfce006`); `tests/test_audio_input.py::test_p14_ac37_nothing_before_start_inputs_then_one_detector_invocation`, `tests/test_audio_input.py::test_p14_ac39_twenty_failures_are_capped_and_degrade_once` (`6668568`) | AC25–AC28, AC37–AC39 |
| R8 a timed random trigger is a setting | `4219e19` | `tests/test_timer.py::test_ac29_two_channels_every_10_minutes_give_12_accepted_events_in_an_hour`, `tests/test_timer.py::test_ac30_100_events_consume_100_draws_and_accept_those_below_the_probability`, `tests/test_timer.py::test_ac31_an_out_of_range_parameter_is_a_load_diagnostic_naming_it` (`4219e19`) | AC29–AC31 |
| R9 documented, summarized, demonstrable from fakes | `7093597`, `5a78832`, `0633b8e`, P16 | `tests/test_declared_triggers.py::test_p15_ac32_the_triggers_guide_is_a_discovered_bilingual_pair`, `tests/test_declared_triggers.py::test_p15_ac32_one_example_per_family_loads_against_the_shipped_manifests` (`7093597`); `tests/test_ac33_isolation.py::test_creating_a_socket_raises` (`5a78832`); `tests/test_ac33_isolation.py::test_the_guard_has_no_ledger` (`0633b8e`); `tests/test_declared_triggers.py::test_p16_ac32_the_summary_cites_r1_to_r9_and_the_trial_is_not_run`, `tests/test_declared_triggers.py::test_ac33_the_whole_suite_runs_under_the_guard`, `tests/test_declared_triggers.py::test_ac33_no_test_reaches_a_real_spawn_or_socket` (P16) | AC32, AC33 |

## Per family: what can and still cannot be triggered

Every family below is proven against fakes only (`docs/triggers.md`, "Manual real-instance trial").
A trigger only decides candidacy; nothing here grants an action.

| Family | Can be triggered | Still cannot |
|---|---|---|
| Channel points (`twitch`, `reward`) | A redemption of a listed reward, when `notices.kinds` lists `reward`; selection by reward id and cost bounds; the viewer's input text, trimmed, reaches the run. | No fulfil or refund: the companion never changes a redemption's status. A refused subscription degrades `reward` only; it does not retry into another scope. |
| Operator button (`operator`, `button`) | A press from the local page, the overlay (as a browser source) or the press command `python -m modules.operator press <button>`, each a `manual` event behind the token and the cooldown. | Binding the press command to a global key is an OS/tool step the guide documents; nothing in the companion binds a key. A `manual` event is not privileged: the brain's route-safety counts it as viewer-reachable. |
| Voice (`audio_input`, `wake_word` hooked, `keyword`) | A detector's phrase decides the candidate before any audio is acquired; one bounded segment is acquired and transcribed per accepted candidate, then one `speech` event. | No continuous listening and no continuous transcription: nothing is recorded unless a trigger or a run asked for it. The detector is configured (a command or an HTTP endpoint), never embedded. |
| Timer (`timer`, `interval`) | A firing every `every_minutes` per configured channel, inside an optional window, accepted with the configured probability. | A `timer` event is not privileged: like `manual`, it counts as viewer-reachable. It never fires before `start_inputs` or after `stop_inputs`. |

## AC33: the whole suite under the guard

`tests/conftest.py`'s autouse `_ac33_isolation` (P1, `5a78832`) makes socket creation (every family,
`create_connection`, `create_server`, `fromfd`, `getaddrinfo`, a `socketpair` other than the event
loop's own wakeup pair) and process spawning (`subprocess.Popen`, which `run` and asyncio's
`create_subprocess_*` reach, `os.system`/`popen`/`posix_spawn*`/`fork*`/`exec*`) raise for every
test. Its only unpatched exit is a real-instance trial node an operator opted in; the default run
opts none in. The three nodes Amendment A2 names run with the real entry points, each call
journaled (see below). The transitional ledger is gone (P15e). P16 checks that every collected item uses the
fixture, that 0 items would run unpatched, and that an `ast` scan of `tests/` finds no call to
`Popen`, `create_subprocess_exec`, `os.system`, `TCPSite`, `TestServer` or `create_connection`
outside the guard's own refusal tests.

### Suite count

One full run under the guard at the evidence head, with the CI command
`.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`:

**4,821 passed, 20 skipped** (P17F2 head; the 20 skips are the opt-in real-instance trials of
`test_phase2_trials.py`, `test_phase3_trials.py` and `test_phase5_trials.py`; no test is xfailed).
The phase's floor was 3,124 passed / 18 skipped.

### The D8 conversion list

The plan's D8 probe counted 166 nodes in 24 files; the probe as re-run and committed in P1
(`tests/test_ac33_isolation.py::AC33_PROBE`) holds **164 nodes in 24 files**, and that committed list
is the one converted. P15a–P15e removed 13 + 24 + 41 + 77 + 9 = 164 nodes from the ledger; P15e
deleted it.

Fidelity lost, per conversion kind:

- **fake-OS only** — the test drives a scripted `FakeOSProcess` or an in-process reader instead of
  a real child (`git`, `grep`, a child interpreter, a recorder, a player, the config-UI child). Real
  process-group kills, signal delivery and child exit codes from a real OS are no longer observed.
- **in-process topology** — the test joins its peers over an in-memory HTTP client, an in-memory
  WebSocket pair or a fed `StreamReader` instead of a loopback listener. A real loopback listener,
  real TCP framing and the real two-process brain/agent topology are no longer observed.
- **no real install** — the test reads the package data and runs the entry points in-process
  instead of a real `pip install` and console-script process.

No real-process stage was added to CI (D8).

**Amendment A2 (P17F2).** The closing gate found that converting every node lost the guarantee
three of them existed for. Those three are no longer converted: they run for real and are named in
`tests/conftest.py::A2_REAL_NODES`, the guard's one list of nodes allowed a socket or a process, and
each socket or spawn they make is journaled in `A2_JOURNAL` (printed as the run's "AC33 Amendment A2
journal" section). Their rows below read **A2**. Every other node is still refused, and
`tests/test_ac33_isolation.py` executes that negative control.

- `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`
  installs with `pip` into an empty target again and asserts the installed layout,
  `report["core"].startswith(installed + os.sep)`, the installed `modules_directory` and
  `launcher.is_file()`; `test_install_test_skips_only_when_pip_is_unavailable` is restored under its
  own name (the P15e replacement `test_install_test_never_skips_and_fails_loudly` is gone).
- `tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`
  reads `git` again, with its work-tree and base-commit existence assertions. Its diff covers the
  shipped profiles only (`'*.example'` with `':(exclude)tests/fixtures/**'`, P17F3), and the committed
  baseline copies exist only as `<file>.baseline` (P17F3 removed the stale `*.example` duplicates).
- `tests/test_kick.py` is byte-identical to `7782e5f`. Its named node binds the real loopback
  listener; the file's other nodes reach the same handler in memory through the conftest fixture
  `_kick_loopback_in_memory` (their `TestClient(TestServer(app))` is an `InMemoryClient`, the listener
  an `InMemoryNetwork` site), so the file is not edited and the guard refuses nothing they open.

P17F2 also put the original assertion sequence back in every other converted test: where a fake
carries the conversion, it now yields the original observations (exit status, stdout/stderr, signal,
ordering, counts), and any check added for the fake's own fidelity lives in a named helper.

The assertion-AST comparison against `7782e5f` (each `test_*` function's ordered `ast.Assert` dumps,
no locations) finds 13 changed existing functions: the 8 allowlisted ones, none of the three A2 nodes,
and 5 outside both. Each of the 5 differs by one expected constant the phase's own requirements changed,
so its phase-7 assertion is false on this tree; they are not conversions and were left as they are:

- `tests/test_audio_input.py::test_prepare_starts_no_task_and_spawns_nothing` — `hasattr(h.handle, "start_inputs")` (R7: `audio_input` is an input).
- `tests/test_audio_input.py::test_the_loader_discovers_the_manifest_and_prepare_binds_the_declared_spec` — `roles == frozenset({"input"})` (R7).
- `tests/test_contracts.py::test_platform_notice_kinds_exclude_message_and_watch_tick` — the excluded set gains the appended kinds (R4).
- `tests/test_twitch.py::test_manifest_declares_twitch_source_and_sink` — `reward` among the declared types, `LISTABLE_KINDS` (R5).
- `tests/test_web.py::test_ac1_the_shipped_manifests_discover_19_modules_with_web` — 19 → 21 (R6, R8).

| Node | Step | Commit | Fidelity lost |
|---|---|---|---|
| `tests/test_agentic_loop.py::test_ac31_the_core_and_the_neutral_modules_name_no_platform` | P15a | `4465d74` | fake-OS only |
| `tests/test_audio_input.py::test_the_real_transport_against_a_closed_port_degrades_the_transcription` | P15c | `4b549f5` | in-process topology |
| `tests/test_audio_input.py::test_the_shipped_runner_is_not_held_by_a_fast_recorder_after_its_segment` | P15b | `709662f` | fake-OS only |
| `tests/test_audio_input.py::test_the_shipped_runner_kills_a_child_left_by_an_exited_wrapper` | P15b | `709662f` | fake-OS only |
| `tests/test_audio_input.py::test_the_shipped_runner_kills_a_silent_real_recorder_at_the_deadline` | P15b | `709662f` | fake-OS only |
| `tests/test_audio_input.py::test_the_shipped_runner_records_a_real_process` | P15b | `709662f` | fake-OS only |
| `tests/test_audio_output.py::test_the_subprocess_runner_feeds_a_real_player_its_stdin` | P15b | `709662f` | fake-OS only |
| `tests/test_automation_bridge.py::test_a_connection_loss_fails_every_pending_request` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_a_cursor_from_another_destination_or_not_minted_here_is_refused` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_a_malformed_viewer_answer_is_a_provider_failure` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_a_platform_the_bridge_does_not_own_is_never_bound_to_it` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_a_withheld_viewer_list_ends_timeout_never_external_unknown` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac15_phase6_a_permanently_raising_trace_keeps_the_backoff` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_no_run_observation_is_a_success` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_acknowledged_is_execution_unconfirmed` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_bridge_not_ready` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_cancelled_before_the_frame_left` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_instance_action_ambiguous` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_instance_action_disabled` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_instance_action_missing` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_instance_error_answer` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_invalid_arguments` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_no_acknowledgement_before_the_deadline` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac16_row_not_allowlisted` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac17_a_late_acknowledgement_is_lost_and_never_re_invoked` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac17_an_answer_landing_while_the_timer_is_cleaned_up_is_lost` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac18_a_group_entry_reaches_its_actions_only` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac18_an_empty_or_absent_allowlist_refuses_everything` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac18_only_allowlisted_names_are_reachable` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac19_the_entry_schema_decides_the_arguments` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac20_reconciliation_names_the_unmatched_entries_once` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac21_a_principal_without_a_rule_is_refused_before_the_provider` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac25_a_failed_start_retries_and_a_success_reports_ready` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac25_a_lost_connection_degrades_at_once_and_reconnects_after_the_minimum` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac25_the_backoff_doubles_from_the_minimum_and_is_capped_at_the_maximum` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac27_owning_only_cues_subscribes_to_the_custom_category_only` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac27_owning_twitch_chat_adds_only_twitch_chat_and_notices` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac28_a_run_in_flight_at_shutdown_is_acknowledgement_lost` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac28_cancelling_the_owner_task_propagates_and_releases_the_connection` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac28_shutdown_closes_within_budget_and_fails_a_pending_request` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac29_a_configured_policy_under_the_bridge_decides_its_cues` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac30_a_cue_without_user_has_one_stable_author_unlike_the_companion` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac30_a_malformed_or_oversized_envelope_is_dropped_and_counted` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac32_instance_roles_satisfy_an_audience_only_when_trusted` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac33_a_mapped_chat_event_authored_by_the_companion_is_refused` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac33_engine_half_an_event_authored_by_the_companion_is_refused` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac33_the_refusal_comes_from_the_identities_the_bridge_hands_over` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac34_a_page_holds_at_most_limit_and_the_cursor_pages_the_same_list` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac34_another_channel_is_refused_unsupported_destination` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac34_the_owned_channel_reads_one_page_of_active_viewers` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac37_the_status_guard_runs_unmarked_and_the_trial_file_keeps_eight_nodes` | P15a | `4465d74` | fake-OS only |
| `tests/test_automation_bridge.py::test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac43_owning_chat_each_fixture_gives_one_event_and_unmapped_is_dropped` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac43_owning_only_twitch_the_kick_and_youtube_fixtures_give_nothing` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac45_every_matching_group_entry_must_accept` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac45_the_action_entry_beats_the_group_entry` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_an_instance_without_a_challenge_is_not_sent_an_authentication` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_d8_a_connection_closed_before_the_segment_is_provider_not_ready` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_d8_an_interruption_in_the_drain_after_the_write_is_acknowledgement_lost` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_d8_behaviour_the_frame_reaches_the_transport_in_the_marking_segment` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_d8_cancelling_after_the_segment_is_acknowledgement_lost` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_d8_negative_control_the_behavioural_check_catches_f2s_mutation` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_d8_the_executor_deadline_before_the_frame_left_is_a_timeout` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_fake_instance_answers_one_hello_and_records_one_frame` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_fake_instance_authentication_switches` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_fake_instance_holds_withholds_and_rejects_acknowledgements` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_fake_instance_malformed_hello_and_failed_inventory` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_fake_instance_pushes_events_echoes_subscriptions_and_closes` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_fake_instance_stop_ends_the_connection` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_r1_a_withheld_inventory_degrades_within_the_startup_bound` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_r5_reconciliation_runs_again_at_every_connection` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_r6_a_refused_subscription_leaves_the_bridge_not_ready` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_r7_a_cue_for_a_platform_not_owned_is_dropped_by_the_bridge` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_r7_a_replayed_cue_id_is_neither_republished_nor_redecided` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_r7_an_accepted_cue_is_admitted_to_the_shared_scheduler` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_r7b_a_replayed_event_without_an_id_is_not_published_twice` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_r7b_mapped_chat_feeds_the_shared_transcript_once` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_the_loader_activates_the_bridge_and_a_run_is_refused_not_ready` | P15d | `8724a2d` | in-process topology |
| `tests/test_automation_bridge.py::test_without_owning_viewers_the_read_is_declared_and_never_bound` | P15d | `8724a2d` | in-process topology |
| `tests/test_capture.py::test_command_exiting_non_zero_is_capture_failed_and_oversize_output_is_refused` | P15b | `709662f` | fake-OS only |
| `tests/test_capture.py::test_command_source_runs_a_python_one_liner_writing_a_png_to_stdout` | P15b | `709662f` | fake-OS only |
| `tests/test_capture.py::test_module_names_no_platform` | P15a | `4465d74` | fake-OS only |
| `tests/test_capture.py::test_run_command_reads_bounded_stdout_and_kills_a_flooding_child` | P15b | `709662f` | fake-OS only |
| `tests/test_chat_context_module.py::test_module_names_no_platform` | P15a | `4465d74` | fake-OS only |
| `tests/test_clips.py::test_ac12_with_only_kick_required_unset_starts_with_the_action_unbound` | P15c | `4b549f5` | in-process topology |
| `tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_ac30_the_launch_argv_runs_without_a_shell` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_ac31_a_child_ignoring_terminate_is_killed_after_the_bounded_wait` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_ac32_after_a_successful_apply_every_page_shows_in_sync` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_d9_a_chatty_child_is_accepted_within_the_window` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_d9_a_refused_report_carries_the_last_line_within_2_kib` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_d9_draining_continues_after_acceptance` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free` | P15b | `709662f` | fake-OS only |
| `tests/test_config_ui.py::test_the_child_inherits_the_environment_plus_the_status_variable` | P15b | `709662f` | fake-OS only |
| `tests/test_hygiene.py::test_n8_the_generated_overlay_and_status_files_are_ignored` | P15a | `4465d74` | fake-OS only |
| `tests/test_input_ownership.py::test_ac14_the_real_users_and_bridge_split_the_viewer_read` | P15c | `4b549f5` | in-process topology |
| `tests/test_input_ownership.py::test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base` | P15a | `4465d74` | fake-OS only |
| `tests/test_input_ownership.py::test_ac44_the_integration_scenarios_count_zero_ownership_rejections` | P15a | `4465d74` | fake-OS only |
| `tests/test_input_ownership.py::test_ac5_a_twitch_event_is_dropped_while_the_owning_bridge_is_down` | P15c | `4b549f5` | in-process topology |
| `tests/test_installation_doc.py::test_the_guides_profile_is_accepted_by_the_real_entry_point` | P15e | `0633b8e` | no real install |
| `tests/test_installation_doc.py::test_the_readme_config_ui_command_runs` | P15e | `0633b8e` | no real install |
| `tests/test_integration.py::test_ac27_example_config_drives_one_correlated_run_and_clean_shutdown` | P15c | `4b549f5` | in-process topology |
| `tests/test_integration.py::test_r8_refused_send_is_sanitized_leaves_no_send_fact_and_next_send_succeeds` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_a_channel_outside_the_configured_ones_is_refused_with_no_request` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_a_delivery_before_start_inputs_is_not_accepted` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_a_delivery_for_an_unconfigured_channel_is_ignored_and_counted` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_a_lost_or_5xx_send_is_external_unknown_and_never_retried` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac24_a_replay_across_the_whole_tolerance_span_is_deduplicated` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac32_an_author_id_with_a_colon_is_refused_and_counted` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac34_a_65536_byte_body_is_still_accepted` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac34_a_valid_delivery_is_published_once_and_admitted_per_policy` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac34_each_refusal_is_answered_4xx_counted_and_publishes_nothing` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac34_the_listener_runs_on_the_configured_host_and_port` | A2 | `0e7a2ab` | none: real, named by A2 |
| `tests/test_kick.py::test_ac34_the_timestamp_bound_is_inclusive_at_300_seconds_either_way` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac35_500_characters_send_one_request_and_501_send_none` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac35_a_429_blocks_sends_until_the_announced_retry_instant` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac35_a_90_second_timeout_is_sent_as_2_minutes` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac35_each_badge_satisfies_its_audience_rule` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac35_each_notice_type_maps_to_its_kind_and_is_admitted_by_kind` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac35_the_channel_account_is_the_broadcaster_by_identity` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_ac35_the_service_refuses_locally_with_no_request` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_an_anonymous_gift_is_published_but_never_admitted` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_an_unlisted_notice_and_an_unmapped_type_are_ignored_and_counted` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_an_unreadable_429_blocks_for_the_bounded_default_and_is_traced` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_chat_owned_elsewhere_publishes_no_delivery_and_keeps_chat_write` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_chat_write_is_bound_on_kick_chat_and_ready_after_prepare` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_each_ban_answer_is_classified_after_one_request` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_each_other_answer_is_classified_after_one_request` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_owning_by_default_a_valid_delivery_is_still_published` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_the_companion_own_messages_are_dropped_before_anything` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_the_dedup_window_is_bounded_by_its_settings` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_the_largest_timeout_is_10080_minutes_and_is_sent` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_the_moderation_service_is_timeout_only_and_no_clip_or_poll_is_published` | P15c | `4b549f5` | in-process topology |
| `tests/test_kick.py::test_the_public_key_is_fetched_once_at_prepare_when_not_configured` | P15c | `4b549f5` | in-process topology |
| `tests/test_main.py::test_the_accepted_limits_bound_the_real_scheduler_memory_and_audit_queue` | P15c | `4b549f5` | in-process topology |
| `tests/test_phase5_docs_status.py::test_ac36_the_socket_count_sees_a_local_and_a_network_socket` | P15e | `0633b8e` | fake-OS only |
| `tests/test_phase5_docs_status.py::test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket` | P15e | `0633b8e` | fake-OS only |
| `tests/test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered` | P15a | `4465d74` | fake-OS only |
| `tests/test_phase5_docs_status.py::test_ac37_the_exclusion_covers_only_the_campaign_papers` | P15a | `4465d74` | fake-OS only |
| `tests/test_profiles.py::test_ac31_the_full_pc_profile_degrades_three_modules_and_still_chats` | P15c | `4b549f5` | in-process topology |
| `tests/test_profiles.py::test_ac43_with_an_unreachable_speech_endpoint_audio_speak_is_named_not_ready_too` | P15c | `4b549f5` | in-process topology |
| `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | A2 | `0e7a2ab` | none: real, named by A2 |
| `tests/test_proxy.py::test_ac31_the_proxy_and_the_agent_link_name_no_platform` | P15a | `4465d74` | fake-OS only |
| `tests/test_proxy.py::test_the_real_listener_cuts_an_oversized_binary_frame_before_the_module_sees_it` | P15e | `0633b8e` | in-process topology |
| `tests/test_proxy_process.py::test_ac39_two_process_topology_over_loopback` | P15e | `0633b8e` | in-process topology |
| `tests/test_shutdown.py::test_cli_deadline_covers_cancellation_resistant_task` | P15b | `709662f` | fake-OS only |
| `tests/test_shutdown.py::test_cli_process_exits_with_synchronous_audit_writer` | P15b | `709662f` | fake-OS only |
| `tests/test_stream_control.py::test_the_default_factory_frames_the_protocol_over_a_real_loopback_socket` | P15e | `0633b8e` | in-process topology |
| `tests/test_users.py::test_module_names_no_platform` | P15a | `4465d74` | fake-OS only |
| `tests/test_viewer_memory.py::test_ac19_cli_the_module_entry_runs_without_a_network_client` | P15a | `4465d74` | fake-OS only |
| `tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base` | A2 | `0e7a2ab` | none: real, named by A2 |
| `tests/test_web.py::test_the_socket_guard_refuses_and_records_every_non_loopback_surface` | P15e | `0633b8e` | in-process topology |
| `tests/test_youtube.py::test_ac39_three_platforms_on_one_runtime` | P15c | `4b549f5` | in-process topology |
