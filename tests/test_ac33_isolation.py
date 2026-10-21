"""The whole-suite isolation guard's own tests (phase 8 R9, AC33; plan P1, D8).

``tests/conftest.py`` installs ``_ac33_isolation`` on every test: creating a
socket or starting a process raises, except the event loop's own wakeup pair.
These tests run under it, so each refusal below is the guard's, and the
``asyncio.run`` that succeeds proves the self-pipe exception holds on this
interpreter. The transitional ledger is gone (P15e converted its last node
and deleted it): :func:`test_the_guard_has_no_ledger` checks no ledger symbol
or branch is left, and :data:`AC33_PROBE` stays as the probe's list committed
at ``7782e5f``, the record of the conversion. The real-instance trials
are exempt only once an operator opts one in, and in the default run none is.
"""

from __future__ import annotations

import ast
import asyncio
import os
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

import conftest
import test_phase2_trials
import test_phase3_trials
import test_phase5_trials
from conftest import trial_opted_in

ROOT = Path(__file__).resolve().parent.parent

#: The D8 probe's list, verbatim: every pre-existing node (``file::test``,
#: parameter suffix stripped) that created a socket other than the event
#: loop's wakeup pair, or started a process, in a full run at ``7782e5f``.
#: This list was the ledger's upper bound and is never edited.
AC33_PROBE: frozenset[str] = frozenset(
    {
        "tests/test_agentic_loop.py::test_ac31_the_core_and_the_neutral_modules_name_no_platform",
        "tests/test_audio_input.py::test_the_real_transport_against_a_closed_port_degrades_the_transcription",
        "tests/test_audio_input.py::test_the_shipped_runner_is_not_held_by_a_fast_recorder_after_its_segment",
        "tests/test_audio_input.py::test_the_shipped_runner_kills_a_child_left_by_an_exited_wrapper",
        "tests/test_audio_input.py::test_the_shipped_runner_kills_a_silent_real_recorder_at_the_deadline",
        "tests/test_audio_input.py::test_the_shipped_runner_records_a_real_process",
        "tests/test_audio_output.py::test_the_subprocess_runner_feeds_a_real_player_its_stdin",
        "tests/test_automation_bridge.py::test_ac15_phase6_a_permanently_raising_trace_keeps_the_backoff",
        "tests/test_automation_bridge.py::test_ac15_phase6_a_raising_reconcile_trace_leaves_the_owner_alive",
        "tests/test_automation_bridge.py::test_ac16_no_run_observation_is_a_success",
        "tests/test_automation_bridge.py::test_ac16_row_acknowledged_is_execution_unconfirmed",
        "tests/test_automation_bridge.py::test_ac16_row_bridge_not_ready",
        "tests/test_automation_bridge.py::test_ac16_row_cancelled_before_the_frame_left",
        "tests/test_automation_bridge.py::test_ac16_row_instance_action_ambiguous",
        "tests/test_automation_bridge.py::test_ac16_row_instance_action_disabled",
        "tests/test_automation_bridge.py::test_ac16_row_instance_action_missing",
        "tests/test_automation_bridge.py::test_ac16_row_instance_error_answer",
        "tests/test_automation_bridge.py::test_ac16_row_invalid_arguments",
        "tests/test_automation_bridge.py::test_ac16_row_no_acknowledgement_before_the_deadline",
        "tests/test_automation_bridge.py::test_ac16_row_not_allowlisted",
        "tests/test_automation_bridge.py::test_ac17_a_late_acknowledgement_is_lost_and_never_re_invoked",
        "tests/test_automation_bridge.py::test_ac17_an_answer_landing_while_the_timer_is_cleaned_up_is_lost",
        "tests/test_automation_bridge.py::test_ac18_a_group_entry_reaches_its_actions_only",
        "tests/test_automation_bridge.py::test_ac18_an_empty_or_absent_allowlist_refuses_everything",
        "tests/test_automation_bridge.py::test_ac18_only_allowlisted_names_are_reachable",
        "tests/test_automation_bridge.py::test_ac19_the_entry_schema_decides_the_arguments",
        "tests/test_automation_bridge.py::test_ac20_reconciliation_names_the_unmatched_entries_once",
        "tests/test_automation_bridge.py::test_ac21_a_principal_without_a_rule_is_refused_before_the_provider",
        "tests/test_automation_bridge.py::test_ac24_the_challenge_answer_and_readiness_only_after_the_inventory",
        "tests/test_automation_bridge.py::test_ac25_a_failed_start_retries_and_a_success_reports_ready",
        "tests/test_automation_bridge.py::test_ac25_a_lost_connection_degrades_at_once_and_reconnects_after_the_minimum",
        "tests/test_automation_bridge.py::test_ac25_the_backoff_doubles_from_the_minimum_and_is_capped_at_the_maximum",
        "tests/test_automation_bridge.py::test_ac26_a_full_queue_drops_and_counts_and_never_stalls_the_connection",
        "tests/test_automation_bridge.py::test_ac27_owning_only_cues_subscribes_to_the_custom_category_only",
        "tests/test_automation_bridge.py::test_ac27_owning_twitch_chat_adds_only_twitch_chat_and_notices",
        "tests/test_automation_bridge.py::test_ac28_a_run_in_flight_at_shutdown_is_acknowledgement_lost",
        "tests/test_automation_bridge.py::test_ac28_cancelling_the_owner_task_propagates_and_releases_the_connection",
        "tests/test_automation_bridge.py::test_ac28_shutdown_closes_within_budget_and_fails_a_pending_request",
        "tests/test_automation_bridge.py::test_ac29_a_configured_policy_under_the_bridge_decides_its_cues",
        "tests/test_automation_bridge.py::test_ac29_a_valid_cue_is_one_normalised_event_the_default_policy_accepts",
        "tests/test_automation_bridge.py::test_ac30_a_cue_without_user_has_one_stable_author_unlike_the_companion",
        "tests/test_automation_bridge.py::test_ac30_a_malformed_or_oversized_envelope_is_dropped_and_counted",
        "tests/test_automation_bridge.py::test_ac32_instance_roles_satisfy_an_audience_only_when_trusted",
        "tests/test_automation_bridge.py::test_ac33_a_mapped_chat_event_authored_by_the_companion_is_refused",
        "tests/test_automation_bridge.py::test_ac33_engine_half_an_event_authored_by_the_companion_is_refused",
        "tests/test_automation_bridge.py::test_ac33_the_refusal_comes_from_the_identities_the_bridge_hands_over",
        "tests/test_automation_bridge.py::test_ac34_another_channel_is_refused_unsupported_destination",
        "tests/test_automation_bridge.py::test_ac34_a_page_holds_at_most_limit_and_the_cursor_pages_the_same_list",
        "tests/test_automation_bridge.py::test_ac34_the_owned_channel_reads_one_page_of_active_viewers",
        "tests/test_automation_bridge.py::test_ac37_the_status_guard_runs_unmarked_and_the_trial_file_keeps_eight_nodes",
        "tests/test_automation_bridge.py::test_ac3_an_unreachable_instance_leaves_a_ready_companion_and_a_degraded_bridge",
        "tests/test_automation_bridge.py::test_ac43_owning_chat_each_fixture_gives_one_event_and_unmapped_is_dropped",
        "tests/test_automation_bridge.py::test_ac43_owning_only_twitch_the_kick_and_youtube_fixtures_give_nothing",
        "tests/test_automation_bridge.py::test_ac45_every_matching_group_entry_must_accept",
        "tests/test_automation_bridge.py::test_ac45_the_action_entry_beats_the_group_entry",
        "tests/test_automation_bridge.py::test_ac4_each_failure_mode_degrades_with_its_reason_and_leaks_nothing",
        "tests/test_automation_bridge.py::test_a_connection_loss_fails_every_pending_request",
        "tests/test_automation_bridge.py::test_a_cursor_from_another_destination_or_not_minted_here_is_refused",
        "tests/test_automation_bridge.py::test_a_malformed_viewer_answer_is_a_provider_failure",
        "tests/test_automation_bridge.py::test_an_instance_without_a_challenge_is_not_sent_an_authentication",
        "tests/test_automation_bridge.py::test_a_platform_the_bridge_does_not_own_is_never_bound_to_it",
        "tests/test_automation_bridge.py::test_a_withheld_viewer_list_ends_timeout_never_external_unknown",
        "tests/test_automation_bridge.py::test_d8_a_connection_closed_before_the_segment_is_provider_not_ready",
        "tests/test_automation_bridge.py::test_d8_an_interruption_in_the_drain_after_the_write_is_acknowledgement_lost",
        "tests/test_automation_bridge.py::test_d8_a_pre_write_failure_sends_nothing_and_withdraws_readiness",
        "tests/test_automation_bridge.py::test_d8_behaviour_the_frame_reaches_the_transport_in_the_marking_segment",
        "tests/test_automation_bridge.py::test_d8_cancelling_after_the_segment_is_acknowledgement_lost",
        "tests/test_automation_bridge.py::test_d8_negative_control_the_behavioural_check_catches_f2s_mutation",
        "tests/test_automation_bridge.py::test_d8_the_executor_deadline_before_the_frame_left_is_a_timeout",
        "tests/test_automation_bridge.py::test_fake_instance_answers_one_hello_and_records_one_frame",
        "tests/test_automation_bridge.py::test_fake_instance_authentication_switches",
        "tests/test_automation_bridge.py::test_fake_instance_holds_withholds_and_rejects_acknowledgements",
        "tests/test_automation_bridge.py::test_fake_instance_malformed_hello_and_failed_inventory",
        "tests/test_automation_bridge.py::test_fake_instance_pushes_events_echoes_subscriptions_and_closes",
        "tests/test_automation_bridge.py::test_fake_instance_stop_ends_the_connection",
        "tests/test_automation_bridge.py::test_r1_a_withheld_inventory_degrades_within_the_startup_bound",
        "tests/test_automation_bridge.py::test_r5_reconciliation_runs_again_at_every_connection",
        "tests/test_automation_bridge.py::test_r6_a_refused_subscription_leaves_the_bridge_not_ready",
        "tests/test_automation_bridge.py::test_r7_a_cue_for_a_platform_not_owned_is_dropped_by_the_bridge",
        "tests/test_automation_bridge.py::test_r7_an_accepted_cue_is_admitted_to_the_shared_scheduler",
        "tests/test_automation_bridge.py::test_r7_a_replayed_cue_id_is_neither_republished_nor_redecided",
        "tests/test_automation_bridge.py::test_r7b_a_replayed_event_without_an_id_is_not_published_twice",
        "tests/test_automation_bridge.py::test_r7b_mapped_chat_feeds_the_shared_transcript_once",
        "tests/test_automation_bridge.py::test_the_loader_activates_the_bridge_and_a_run_is_refused_not_ready",
        "tests/test_automation_bridge.py::test_without_owning_viewers_the_read_is_declared_and_never_bound",
        "tests/test_capture.py::test_command_exiting_non_zero_is_capture_failed_and_oversize_output_is_refused",
        "tests/test_capture.py::test_command_source_runs_a_python_one_liner_writing_a_png_to_stdout",
        "tests/test_capture.py::test_module_names_no_platform",
        "tests/test_capture.py::test_run_command_reads_bounded_stdout_and_kills_a_flooding_child",
        "tests/test_chat_context_module.py::test_module_names_no_platform",
        "tests/test_clips.py::test_ac12_with_only_kick_required_unset_starts_with_the_action_unbound",
        "tests/test_config_ui.py::test_ac30_a_child_exiting_2_is_refused_with_its_status_and_diagnostic",
        "tests/test_config_ui.py::test_ac30_a_child_writing_nothing_within_the_window_is_unknown",
        "tests/test_config_ui.py::test_ac30_a_ready_record_of_the_child_with_the_on_disk_digest_is_accepted",
        "tests/test_config_ui.py::test_ac30_the_launch_argv_runs_without_a_shell",
        "tests/test_config_ui.py::test_ac31_a_child_ignoring_terminate_is_killed_after_the_bounded_wait",
        "tests/test_config_ui.py::test_ac31_apply_never_signals_a_foreign_record_pid",
        "tests/test_config_ui.py::test_ac32_after_a_successful_apply_every_page_shows_in_sync",
        "tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record",
        "tests/test_config_ui.py::test_ac39_ac40_a_child_writing_only_an_unaccepted_record_is_unknown",
        "tests/test_config_ui.py::test_d9_a_chatty_child_is_accepted_within_the_window",
        "tests/test_config_ui.py::test_d9_a_refused_report_carries_the_last_line_within_2_kib",
        "tests/test_config_ui.py::test_d9_draining_continues_after_acceptance",
        "tests/test_config_ui.py::test_d9_the_refused_tail_is_redacted_and_value_free",
        "tests/test_config_ui.py::test_the_child_inherits_the_environment_plus_the_status_variable",
        "tests/test_hygiene.py::test_n8_the_generated_overlay_and_status_files_are_ignored",
        "tests/test_input_ownership.py::test_ac14_the_real_users_and_bridge_split_the_viewer_read",
        "tests/test_input_ownership.py::test_ac2_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base",
        "tests/test_input_ownership.py::test_ac44_the_integration_scenarios_count_zero_ownership_rejections",
        "tests/test_input_ownership.py::test_ac5_a_twitch_event_is_dropped_while_the_owning_bridge_is_down",
        "tests/test_installation_doc.py::test_the_guides_profile_is_accepted_by_the_real_entry_point",
        "tests/test_installation_doc.py::test_the_readme_config_ui_command_runs",
        "tests/test_integration.py::test_ac27_example_config_drives_one_correlated_run_and_clean_shutdown",
        "tests/test_integration.py::test_r8_refused_send_is_sanitized_leaves_no_send_fact_and_next_send_succeeds",
        "tests/test_kick.py::test_ac24_a_replay_across_the_whole_tolerance_span_is_deduplicated",
        "tests/test_kick.py::test_ac32_an_author_id_with_a_colon_is_refused_and_counted",
        "tests/test_kick.py::test_ac34_a_65536_byte_body_is_still_accepted",
        "tests/test_kick.py::test_ac34_a_valid_delivery_is_published_once_and_admitted_per_policy",
        "tests/test_kick.py::test_ac34_each_refusal_is_answered_4xx_counted_and_publishes_nothing",
        "tests/test_kick.py::test_ac34_the_listener_runs_on_the_configured_host_and_port",
        "tests/test_kick.py::test_ac34_the_timestamp_bound_is_inclusive_at_300_seconds_either_way",
        "tests/test_kick.py::test_ac35_500_characters_send_one_request_and_501_send_none",
        "tests/test_kick.py::test_ac35_a_429_blocks_sends_until_the_announced_retry_instant",
        "tests/test_kick.py::test_ac35_a_90_second_timeout_is_sent_as_2_minutes",
        "tests/test_kick.py::test_ac35_each_badge_satisfies_its_audience_rule",
        "tests/test_kick.py::test_ac35_each_notice_type_maps_to_its_kind_and_is_admitted_by_kind",
        "tests/test_kick.py::test_ac35_the_channel_account_is_the_broadcaster_by_identity",
        "tests/test_kick.py::test_ac35_the_service_refuses_locally_with_no_request",
        "tests/test_kick.py::test_a_channel_outside_the_configured_ones_is_refused_with_no_request",
        "tests/test_kick.py::test_a_delivery_before_start_inputs_is_not_accepted",
        "tests/test_kick.py::test_a_delivery_for_an_unconfigured_channel_is_ignored_and_counted",
        "tests/test_kick.py::test_a_lost_or_5xx_send_is_external_unknown_and_never_retried",
        "tests/test_kick.py::test_an_anonymous_gift_is_published_but_never_admitted",
        "tests/test_kick.py::test_an_unlisted_notice_and_an_unmapped_type_are_ignored_and_counted",
        "tests/test_kick.py::test_an_unreadable_429_blocks_for_the_bounded_default_and_is_traced",
        "tests/test_kick.py::test_chat_owned_elsewhere_publishes_no_delivery_and_keeps_chat_write",
        "tests/test_kick.py::test_chat_write_is_bound_on_kick_chat_and_ready_after_prepare",
        "tests/test_kick.py::test_each_ban_answer_is_classified_after_one_request",
        "tests/test_kick.py::test_each_other_answer_is_classified_after_one_request",
        "tests/test_kick.py::test_owning_by_default_a_valid_delivery_is_still_published",
        "tests/test_kick.py::test_the_companion_own_messages_are_dropped_before_anything",
        "tests/test_kick.py::test_the_dedup_window_is_bounded_by_its_settings",
        "tests/test_kick.py::test_the_largest_timeout_is_10080_minutes_and_is_sent",
        "tests/test_kick.py::test_the_moderation_service_is_timeout_only_and_no_clip_or_poll_is_published",
        "tests/test_kick.py::test_the_public_key_is_fetched_once_at_prepare_when_not_configured",
        "tests/test_main.py::test_the_accepted_limits_bound_the_real_scheduler_memory_and_audit_queue",
        "tests/test_phase5_docs_status.py::test_ac36_the_socket_count_sees_a_local_and_a_network_socket",
        "tests/test_phase5_docs_status.py::test_ac36_the_trial_file_skips_its_eight_nodes_opening_no_socket",
        "tests/test_phase5_docs_status.py::test_ac37_no_document_calls_the_bridge_delivered",
        "tests/test_phase5_docs_status.py::test_ac37_the_exclusion_covers_only_the_campaign_papers",
        "tests/test_profiles.py::test_ac31_the_full_pc_profile_degrades_three_modules_and_still_chats",
        "tests/test_profiles.py::test_ac43_with_an_unreachable_speech_endpoint_audio_speak_is_named_not_ready_too",
        "tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help",
        "tests/test_proxy_process.py::test_ac39_two_process_topology_over_loopback",
        "tests/test_proxy.py::test_ac31_the_proxy_and_the_agent_link_name_no_platform",
        "tests/test_proxy.py::test_the_real_listener_cuts_an_oversized_binary_frame_before_the_module_sees_it",
        "tests/test_shutdown.py::test_cli_deadline_covers_cancellation_resistant_task",
        "tests/test_shutdown.py::test_cli_process_exits_with_synchronous_audit_writer",
        "tests/test_stream_control.py::test_the_default_factory_frames_the_protocol_over_a_real_loopback_socket",
        "tests/test_users.py::test_module_names_no_platform",
        "tests/test_viewer_memory.py::test_ac19_cli_the_module_entry_runs_without_a_network_client",
        "tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base",
        "tests/test_web.py::test_the_socket_guard_refuses_and_records_every_non_loopback_surface",
        "tests/test_youtube.py::test_ac39_three_platforms_on_one_runtime",
    }
)


def _socket_refused(request: pytest.FixtureRequest) -> str:
    return f"AC33: {request.node.nodeid} created a socket"


def _process_refused(request: pytest.FixtureRequest) -> str:
    return f"AC33: {request.node.nodeid} started a process"


# --------------------------------------------------------------------------- #
# Sockets
# --------------------------------------------------------------------------- #


def test_creating_a_socket_raises(request: pytest.FixtureRequest) -> None:
    with pytest.raises(AssertionError) as refused:
        socket.socket()
    assert str(refused.value) == _socket_refused(request)


def test_creating_a_unix_socket_raises(request: pytest.FixtureRequest) -> None:
    with pytest.raises(AssertionError) as refused:
        socket.socket(socket.AF_UNIX)
    assert str(refused.value) == _socket_refused(request)


def test_connecting_raises(request: pytest.FixtureRequest) -> None:
    with pytest.raises(AssertionError) as refused:
        socket.create_connection(("127.0.0.1", 1))
    assert str(refused.value) == _socket_refused(request)


def test_a_direct_socketpair_raises(request: pytest.FixtureRequest) -> None:
    with pytest.raises(AssertionError) as refused:
        socket.socketpair()
    assert str(refused.value) == _socket_refused(request)


def test_the_other_socket_paths_raise(request: pytest.FixtureRequest) -> None:
    for attempt in (
        lambda: socket.create_server(("127.0.0.1", 0)),
        lambda: socket.fromfd(0, socket.AF_INET, socket.SOCK_STREAM),
        lambda: socket.getaddrinfo("localhost", 80),
    ):
        with pytest.raises(AssertionError) as refused:
            attempt()
        assert str(refused.value) == _socket_refused(request)


def test_the_event_loop_wakeup_pair_is_the_one_exception() -> None:
    """The self-pipe is matched by its caller's qualified name: a loop starts."""

    asyncio.run(asyncio.sleep(0))


# --------------------------------------------------------------------------- #
# Processes
# --------------------------------------------------------------------------- #


def test_subprocess_run_raises(request: pytest.FixtureRequest) -> None:
    with pytest.raises(AssertionError) as refused:
        subprocess.run(["true"])
    assert str(refused.value) == _process_refused(request)


def test_asyncio_subprocess_raises(request: pytest.FixtureRequest) -> None:
    with pytest.raises(AssertionError) as refused:
        asyncio.run(asyncio.create_subprocess_exec("true"))
    assert str(refused.value) == _process_refused(request)


def test_os_system_raises(request: pytest.FixtureRequest) -> None:
    with pytest.raises(AssertionError) as refused:
        os.system("true")
    assert str(refused.value) == _process_refused(request)


def test_the_other_process_paths_raise(request: pytest.FixtureRequest) -> None:
    """Each is called with an arity its real function refuses (``TypeError``,
    before anything starts), so only the guard's own refusal passes here."""

    for name in (
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "forkpty",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
    ):
        with pytest.raises(AssertionError) as refused:
            getattr(os, name)(*(None,) * 7)
        assert str(refused.value) == _process_refused(request), name


# --------------------------------------------------------------------------- #
# No ledger left (P15e)
# --------------------------------------------------------------------------- #


def test_the_guard_has_no_ledger() -> None:
    """AC33 (R9, plan P15e): the transitional ledger is gone. ``conftest``
    defines no ``AC33_PENDING`` symbol (nor its node-key helper), its source
    names none, and the only early, unpatched exit of ``_ac33_isolation`` is
    the operator's trial opt-in."""

    assert not hasattr(conftest, "AC33_PENDING")
    assert not hasattr(conftest, "ac33_node")
    source = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "AC33_PENDING" not in source
    (fixture,) = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_ac33_isolation"
    ]
    exits = [
        node
        for node in ast.walk(fixture)
        if isinstance(node, ast.If) and any(isinstance(item, ast.Return) for item in node.body)
    ]
    assert len(exits) == 1
    (condition,) = exits
    assert isinstance(condition.test, ast.Call)
    assert isinstance(condition.test.func, ast.Name)
    assert condition.test.func.id == "trial_opted_in"


def test_the_probe_list_is_the_committed_record() -> None:
    """The D8 probe's list stays as committed at ``7782e5f`` (the phase
    summary's conversion list); every node on it now runs under the guard."""

    assert len(AC33_PROBE) == 164
    assert len({entry.split("::")[0] for entry in AC33_PROBE}) == 24
    assert all("[" not in entry for entry in AC33_PROBE)
    assert all(entry.startswith("tests/test_") and "::" in entry for entry in AC33_PROBE)


# --------------------------------------------------------------------------- #
# The operator trials
# --------------------------------------------------------------------------- #

TRIAL_FILES = {
    "test_phase2_trials.py": test_phase2_trials,
    "test_phase3_trials.py": test_phase3_trials,
    "test_phase5_trials.py": test_phase5_trials,
}


def _trial_nodes(module: Any) -> list[str]:
    return sorted(name for name in vars(module) if name.startswith("test_trial_"))


def _switches(file_name: str, module: Any, test_name: str) -> tuple[str, ...]:
    """The variables that opt *test_name* in, read from its file's own tables."""

    if file_name == "test_phase5_trials.py":
        return module.QUESTIONS[module.NODES[test_name]][1]
    source = Path(module.__file__).read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name
    )
    gate = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("required", "gate")
    )
    argument = getattr(module, gate.args[0].id)
    if file_name == "test_phase3_trials.py":
        return module.TRIAL_VARIABLES[argument]
    return (argument,)


def _every_switch() -> set[str]:
    return {
        variable
        for file_name, module in TRIAL_FILES.items()
        for test_name in _trial_nodes(module)
        for variable in _switches(file_name, module, test_name)
    }


def test_no_trial_node_is_exempt_without_its_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """In the default run (no opt-in variable set) the guard covers every trial."""

    for variable in _every_switch():
        monkeypatch.delenv(variable, raising=False)
    nodes = [
        (file_name, module, test_name)
        for file_name, module in TRIAL_FILES.items()
        for test_name in _trial_nodes(module)
    ]
    assert len(nodes) == 20
    assert [node for node in nodes if trial_opted_in(*node)] == []


def test_an_opted_in_trial_node_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exemption follows each file's own gate, node by node."""

    for file_name, module in TRIAL_FILES.items():
        for test_name in _trial_nodes(module):
            for variable in _every_switch():
                monkeypatch.delenv(variable, raising=False)
            for variable in _switches(file_name, module, test_name):
                monkeypatch.setenv(variable, "configured")
            assert trial_opted_in(file_name, module, test_name), (file_name, test_name)
            others = [
                other
                for other in _trial_nodes(module)
                if other != test_name
                and not set(_switches(file_name, module, other))
                <= set(_switches(file_name, module, test_name))
            ]
            assert not any(trial_opted_in(file_name, module, other) for other in others)
