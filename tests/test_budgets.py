"""Acted per-run budgets and the generic fallback (R3; AC13–AC19).

Every budget is finite, validated and acted at its own boundary: model
turns before another model call, repeated proposals and action calls
before a proposal is executed, cumulative tokens on every reply, the model
call's time bound on the injected clock, an action's time bound as the
executor's own ``timeout`` observation fed back to the model, and the
delivery reserve before every model turn and every non-delivery action.
Exceeding a budget ends the run ``error`` with ``failure: budget_exhausted``
and ``budget: <name>``; reaching the reserve ends it ``error`` with
``failure: deadline_reserve``; a model call over ``model_call_seconds`` ends
it ``timeout``.

A run that ends this way — or a queued work the scheduler drops stale — has
no delivered final response, so the fallback policy runs: ``fallback.text``
goes through the run's resolved delivery list exactly as a final response
would, under the same principal, call ids and action budget, and the run
reports ``fallback: sent`` with ``delivery: fallback:<summary>``; when the
policy is disabled, no entry has an applicable rule, no action call is left
or the total deadline has passed, nothing is sent and ``fallback`` names why.
The run's status stays the failure status either way.

The harness is the agentic-loop one: the shipped brain, the real executor,
scheduler and store, read doubles bound in the test. Nothing sleeps: the
clock is injected and every wait is a bounded number of bare loop turns.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.actions import ERROR_TIMED_OUT
from core.admission import REASON_COMPLETED, REASON_RUN_DEADLINE, REASON_STALE_DROP
from core.contracts import RUN_FAILURE_BUDGET_EXHAUSTED
from conftest import FakeResponse, HeldSession, final, settle, tool_call, wait_until
from modules.brain import (
    DELIVERY_NOT_ATTEMPTED,
    FALLBACK_NONE,
    FALLBACK_SENT,
    FALLBACK_SKIPPED_BUDGET_EXHAUSTED,
    FALLBACK_SKIPPED_DEADLINE_EXCEEDED,
    FALLBACK_SKIPPED_DISABLED,
    FALLBACK_SKIPPED_NOT_AUTHORIZED,
    PRINCIPAL,
    RUN_FAILURE_DEADLINE_RESERVE,
)
from test_agentic_loop import (
    CHANNEL,
    CHAT_READ,
    CHAT_SCOPE,
    CHAT_WRITE,
    FINAL_TEXT,
    PLATFORM,
    SCREEN_CAPTURE,
    USERS_READ,
    activate_loop,
    observation_envelope,
    tool_messages,
)
from test_brain import VALID_SETTINGS

FALLBACK_TEXT = VALID_SETTINGS["fallback"]["text"]
assert FALLBACK_TEXT == "I could not answer in time."

# AC17's window: a minute of total budget, the last ten seconds of it the
# terminal step's. The model call bound is widened in the reserve scenarios
# so the clock can be walked into the reserve while a request is held.
AC17_ADMISSION = {"wait_seconds": 30, "total_run_seconds": 60}
AC17_BUDGET = {"delivery_reserve_seconds": 10}


def assert_exhausted(harness, record, budget: str) -> dict:
    """The record and its trace read ``error`` / ``budget_exhausted`` / *budget*."""

    assert record.status == "error"
    assert record.reason == REASON_COMPLETED
    completed = harness.run_completed()
    assert completed["status"] == "error"
    assert completed["failure"] == RUN_FAILURE_BUDGET_EXHAUSTED
    assert completed["budget"] == budget
    return completed


def assert_fallback_sent(harness, record, call_id: str) -> dict:
    """The fallback text left through the single ``chat.write`` entry (R3).

    ``fallback: sent``, ``delivery: fallback:success``, one ``deliveries``
    entry under *call_id* carrying the text, exactly one send whose text is
    ``fallback.text`` under the brain's principal — and the run's status is
    still the failure the loop ended with, never ``success``.
    """

    completed = harness.run_completed()
    assert completed["fallback"] == FALLBACK_SENT
    assert completed["delivery"] == "fallback:success"
    assert record.delivery == "fallback:success"
    assert record.status != "success"
    assert completed["deliveries"] == [
        {"action": CHAT_WRITE, "call_id": call_id, "text": True, "status": "success"}
    ]
    assert completed["sends"] == 1
    (send,) = harness.sender.sends
    assert send["text"] == FALLBACK_TEXT
    assert send["call_id"] == call_id
    assert send["run_id"] == record.run_id
    assert send["principal"] == PRINCIPAL
    assert send["destination"] == f"{PLATFORM}/{CHANNEL}/{CHAT_SCOPE}"
    assert harness.memory() == []
    return completed


def assert_fallback_skipped(harness, record, reason: str) -> dict:
    """Nothing left: ``fallback: skipped:<reason>``, no entry reached, 0 sends."""

    completed = harness.run_completed()
    assert completed["fallback"] == reason
    assert completed["deliveries"] == []
    assert completed["delivery"] == DELIVERY_NOT_ATTEMPTED
    assert record.delivery == DELIVERY_NOT_ATTEMPTED
    assert completed["sends"] == 0
    assert harness.sender.sends == []
    assert harness.sender.invocations == 0
    assert harness.memory() == []
    return completed


# --------------------------------------------------------------------------- #
# AC13: model turns, then the fallback
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac13_the_model_turn_budget_stops_the_loop_and_the_fallback_is_delivered() -> None:
    """AC13 (R3): with ``model_turns: 2`` and a model proposing ``chat.read``
    on every turn, the run ends ``error``, ``failure: budget_exhausted``,
    ``budget: model_turns`` after exactly 2 model requests — both proposals
    executed, no third request built — and the fallback text is delivered
    by exactly 1 ``chat.write`` call continuing the run's counter
    (``delivery == "fallback:success"``, ``fallback == "sent"``)."""

    harness = await activate_loop(
        *(tool_call(CHAT_READ, {"limit": 3}) for _ in range(3)),
        settings_overrides={"budget": {"model_turns": 2}},
    )
    try:
        record = await harness.ask()
        completed = assert_exhausted(harness, record, "model_turns")
        assert len(harness.requests()) == 2
        assert len(harness.reader.calls) == 2
        assert len(harness.session.results) == 1  # the third body was never asked for
        assert "brain run: budget exhausted: model_turns" in harness.diagnostics

        assert_fallback_sent(harness, record, f"{record.run_id}/call-3")
        assert (completed["turns"], completed["action_calls"]) == (2, 3)
        assert harness.executor_calls() == [
            (CHAT_READ, f"{record.run_id}/call-1"),
            (CHAT_READ, f"{record.run_id}/call-2"),
            (CHAT_WRITE, f"{record.run_id}/call-3"),
        ]
        # The fallback answers nothing the model said: the text is the
        # configured one, and the third scripted body was never fetched.
        assert FINAL_TEXT not in [send["text"] for send in harness.sender.sends]
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC14: repeated proposals and action calls
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac14_the_third_identical_proposal_is_not_executed_and_a_different_one_is() -> None:
    """AC14 (R3): with ``max_repeated_actions: 2``, the third identical
    ``chat.read`` proposal — same name, same canonical arguments, whatever
    the key order — is not executed: 2 executor calls for it, run ``error``,
    ``budget: max_repeated_actions``, and the fallback goes out as its third
    call. A third ``chat.read`` with a different ``limit`` is executed, and
    the run goes on to its final response with no fallback."""

    repeated = await activate_loop(
        tool_call(CHAT_READ, {"limit": 3}),
        tool_call(CHAT_READ, '{"limit": 3}'),
        tool_call(CHAT_READ, {"limit": 3}),
        final("never"),
        settings_overrides={"budget": {"max_repeated_actions": 2}},
    )
    try:
        record = await repeated.ask()
        completed = assert_exhausted(repeated, record, "max_repeated_actions")
        assert len(repeated.reader.calls) == 2
        assert [call.call_id for call in repeated.reader.calls] == [
            f"{record.run_id}/call-1",
            f"{record.run_id}/call-2",
        ]
        assert len(repeated.requests()) == 3
        assert_fallback_sent(repeated, record, f"{record.run_id}/call-3")
        assert (completed["turns"], completed["action_calls"]) == (3, 3)
    finally:
        await repeated.close()

    varied = await activate_loop(
        tool_call(CHAT_READ, {"limit": 3}),
        tool_call(CHAT_READ, {"limit": 3}),
        tool_call(CHAT_READ, {"limit": 4}),
        final(FINAL_TEXT),
        settings_overrides={"budget": {"max_repeated_actions": 2}},
    )
    try:
        record = await varied.ask()
        assert record.status == "success"
        assert len(varied.reader.calls) == 3
        assert [call.arguments["limit"] for call in varied.reader.calls] == [3, 3, 4]
        completed = varied.run_completed()
        assert (completed["turns"], completed["action_calls"]) == (4, 4)
        assert completed["fallback"] == FALLBACK_NONE
        assert [send["text"] for send in varied.sender.sends] == [FINAL_TEXT]
        assert varied.sender.sends[0]["call_id"] == f"{record.run_id}/call-4"
    finally:
        await varied.close()


@pytest.mark.asyncio
async def test_ac18_the_action_call_budget_stops_a_proposal_and_leaves_no_call_for_the_fallback() -> None:
    """R3 / AC18 (consumed budget): a proposal with no action call left is
    not executed — the run ends ``error``, ``budget: max_action_calls`` with
    exactly ``max_action_calls`` executor calls made, the repeated-action
    budget wide enough not to be what stops it — and, the budget every
    delivery entry counts against being spent, the fallback sends 0
    messages and records ``skipped:budget_exhausted``."""

    harness = await activate_loop(
        *(tool_call(CHAT_READ, {"limit": index + 1}) for index in range(4)),
        settings_overrides={"budget": {"max_action_calls": 2, "max_repeated_actions": 5}},
    )
    try:
        record = await harness.ask()
        completed = assert_exhausted(harness, record, "max_action_calls")
        assert len(harness.reader.calls) == 2
        assert (completed["turns"], completed["action_calls"]) == (3, 2)
        assert len(harness.requests()) == 3
        assert_fallback_skipped(harness, record, FALLBACK_SKIPPED_BUDGET_EXHAUSTED)
        assert harness.executor_calls() == [
            (CHAT_READ, f"{record.run_id}/call-1"),
            (CHAT_READ, f"{record.run_id}/call-2"),
        ]
        assert (
            f"brain fallback: {FALLBACK_SKIPPED_BUDGET_EXHAUSTED} after {RUN_FAILURE_BUDGET_EXHAUSTED}"
            in harness.diagnostics
        )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_final_response_with_no_action_call_left_ends_error_and_keeps_its_delivery_summary() -> None:
    """R3: a final response reached after exactly ``max_action_calls``
    executor calls has no call left for its terminal step — the entry is
    recorded ``skipped:budget_exhausted`` and nothing is sent — and the run
    ends on a valid terminal state, ``error`` with ``failure:
    budget_exhausted`` and ``budget: max_action_calls``, its ``delivery``
    keeping the summary and its accounting intact: a skipped delivery is
    never a lost record, and no fallback follows it under the same spent
    budget."""

    harness = await activate_loop(
        tool_call(CHAT_READ, {"limit": 1}),
        tool_call(CHAT_READ, {"limit": 2}),
        final(FINAL_TEXT),
        settings_overrides={"budget": {"max_action_calls": 2, "max_repeated_actions": 5}},
    )
    try:
        record = await harness.ask()
        completed = assert_exhausted(harness, record, "max_action_calls")
        assert record.delivery == FALLBACK_SKIPPED_BUDGET_EXHAUSTED
        assert completed["delivery"] == FALLBACK_SKIPPED_BUDGET_EXHAUSTED
        assert completed["deliveries"] == [
            {
                "action": CHAT_WRITE,
                "call_id": None,
                "text": True,
                "status": FALLBACK_SKIPPED_BUDGET_EXHAUSTED,
            }
        ]
        assert "delivery_error" not in completed
        assert completed["fallback"] == FALLBACK_NONE
        assert (completed["turns"], completed["action_calls"]) == (3, 2)
        assert (completed["model_calls"], completed["sends"]) == (3, 0)
        assert len(harness.reader.calls) == 2
        assert harness.executor_calls() == [
            (CHAT_READ, f"{record.run_id}/call-1"),
            (CHAT_READ, f"{record.run_id}/call-2"),
        ]
        assert harness.sender.sends == []
        assert harness.memory() == []
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC15: cumulative tokens
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac15_reported_usage_over_max_tokens_ends_the_run_and_an_estimate_is_flagged() -> None:
    """AC15 (R3): with ``max_tokens: 200`` and a backend reporting
    ``usage.total_tokens: 250`` on the first turn, the run ends ``error``,
    ``budget: max_tokens``, ``tokens: 250``, ``tokens_estimated: false``;
    without ``usage`` the recorded ``tokens_estimated`` is ``true``. Both
    are spent budgets, so both deliver the fallback as their first call."""

    reported = await activate_loop(
        tool_call(CHAT_READ, {"limit": 1}, usage={"total_tokens": 250}),
        final("never"),
        settings_overrides={"budget": {"max_tokens": 200}},
        grants=(CHAT_WRITE,),
    )
    try:
        record = await reported.ask()
        completed = assert_exhausted(reported, record, "max_tokens")
        assert completed["tokens"] == 250
        assert completed["tokens_estimated"] is False
        assert len(reported.requests()) == 1
        assert reported.reader.calls == []
        assert_fallback_sent(reported, record, f"{record.run_id}/call-1")
        assert completed["action_calls"] == 1
    finally:
        await reported.close()

    estimated = await activate_loop(
        final("x" * 900),
        settings_overrides={"budget": {"max_tokens": 200}},
        grants=(CHAT_WRITE,),
    )
    try:
        record = await estimated.ask()
        completed = assert_exhausted(estimated, record, "max_tokens")
        assert completed["tokens_estimated"] is True
        assert completed["tokens"] > 200
        assert len(estimated.requests()) == 1
        assert 0 < estimated.requests()[0]["max_tokens"] < 200
        assert_fallback_sent(estimated, record, f"{record.run_id}/call-1")
        # The over-budget reply is discarded: its text never leaves.
        assert "x" * 900 not in [send["text"] for send in estimated.sender.sends]
    finally:
        await estimated.close()


@pytest.mark.asyncio
async def test_tokens_accumulate_over_the_turns_of_one_run() -> None:
    """R3: ``max_tokens`` is cumulative input + output per run — two replies
    each under the bound but over it together end the run on the second,
    with the sum recorded; the request's output cap is what the run has
    left; the fallback continues the run's call counter."""

    harness = await activate_loop(
        tool_call(CHAT_READ, {"limit": 1}, usage={"total_tokens": 600}),
        tool_call(CHAT_READ, {"limit": 2}, usage={"total_tokens": 600}),
        final("never"),
        settings_overrides={"budget": {"max_tokens": 1000}},
        grants=(CHAT_READ, CHAT_WRITE),
    )
    try:
        record = await harness.ask()
        completed = assert_exhausted(harness, record, "max_tokens")
        assert completed["tokens"] == 1200
        assert completed["tokens_estimated"] is False
        assert len(harness.requests()) == 2
        first, second = harness.requests()
        assert first["max_tokens"] > 600 > second["max_tokens"] > 0
        assert_fallback_sent(harness, record, f"{record.run_id}/call-2")
        assert completed["action_calls"] == 2
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC16: time bounds on the injected clock
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac16_a_model_request_held_past_model_call_seconds_ends_the_run_timeout() -> None:
    """AC16 (R3), model side: advancing the injected clock 31 s while a
    model request is held ends the run ``timeout`` with exactly 1 model
    request, the held call still counted. A model call's own time bound is
    neither a spent budget nor the total deadline: no fallback runs."""

    held = HeldSession(FakeResponse(200, final("late")))
    harness = await activate_loop(session=held)  # type: ignore[arg-type]
    try:
        await harness.send()
        await asyncio.wait_for(held.entered.wait(), 1)
        harness.clock.advance(30.0)
        await asyncio.sleep(0)
        assert harness.handle.scheduler.run_records() == {}
        harness.clock.advance(1.0)
        record = await harness.completed()
        assert record.status == "timeout"
        assert record.model_calls == 1
        assert len(held.post_calls) == 1
        completed = harness.run_completed()
        assert completed["failure"] == "timed_out"
        assert completed["turns"] == 1
        assert completed["fallback"] == FALLBACK_NONE
        assert harness.sender.sends == []
        held.release.set()
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac16_a_capture_held_past_action_seconds_is_a_timeout_observation_fed_to_the_model() -> None:
    """AC16 (R3), action side: advancing the clock 11 s while a
    ``screen.capture`` call is held yields the executor's ``timeout``
    observation — fed to the model as the tool result, the late image
    released — and, with a scripted final response next, the run ends
    ``success``."""

    harness = await activate_loop(tool_call(SCREEN_CAPTURE, {}), final(FINAL_TEXT))
    harness.capture.hold = asyncio.get_running_loop().create_future()
    try:
        await harness.send()
        await asyncio.wait_for(harness.capture.entered.wait(), 1)
        harness.clock.advance(11.0)
        harness.capture.hold.set_result(None)
        record = await harness.completed()
        assert record.status == "success"
        run_id = record.run_id
        timed_out = harness.executor.outcome(f"{run_id}/call-1")
        assert timed_out.status == "timeout"
        assert timed_out.error["code"] == ERROR_TIMED_OUT
        (result,) = tool_messages(harness.requests()[1])
        assert observation_envelope(result) == {"status": "timeout", "error": {"code": ERROR_TIMED_OUT}}
        assert harness.store.usage(run_id).objects == 0
        assert harness.sender.sends[0]["text"] == FINAL_TEXT
        completed = harness.run_completed()
        assert (completed["turns"], completed["action_calls"]) == (2, 2)
        assert completed["fallback"] == FALLBACK_NONE
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC17: the delivery reserve and the total deadline
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac17_a_proposal_inside_the_reserve_is_not_executed_and_the_fallback_is_delivered() -> None:
    """AC17 (R3), reserve: with ``total_run_seconds: 60`` and
    ``delivery_reserve_seconds: 10``, a run whose clock reaches 51 s after
    admission while the model is proposing another action makes 0 further
    model requests, executes 0 further actions — the proposal is stopped
    before the executor, ``failure: deadline_reserve`` — and delivers the
    fallback inside the reserve: 1 send, ``fallback: sent``."""

    held = HeldSession(FakeResponse(200, tool_call(CHAT_READ, {"limit": 2})))
    harness = await activate_loop(
        session=held,  # type: ignore[arg-type]
        settings_overrides={
            "admission": AC17_ADMISSION,
            "budget": {**AC17_BUDGET, "model_call_seconds": 55},
        },
    )
    try:
        await harness.send()
        await asyncio.wait_for(held.entered.wait(), 1)
        admitted_at = harness.clock()
        harness.clock.advance(51.0)
        held.release.set()
        record = await harness.completed()

        assert record.status == "error"
        assert record.reason == REASON_COMPLETED
        completed = harness.run_completed()
        assert completed["failure"] == RUN_FAILURE_DEADLINE_RESERVE
        assert "budget" not in completed
        assert len(held.post_calls) == 1
        assert harness.reader.calls == []
        assert_fallback_sent(harness, record, f"{record.run_id}/call-1")
        assert (completed["turns"], completed["action_calls"]) == (1, 1)
        assert harness.executor_calls() == [(CHAT_WRITE, f"{record.run_id}/call-1")]
        # The fallback left inside the reserve, before the total deadline.
        assert harness.clock() - admitted_at == pytest.approx(51.0)
        assert "brain run: delivery reserve reached before the next action" in harness.diagnostics
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac17_no_model_turn_starts_inside_the_reserve() -> None:
    """AC17 (R3), model side of the reserve: an action whose observation
    lands with less than the reserve left is fed to no further model turn —
    0 further requests — and the fallback is delivered."""

    harness = await activate_loop(
        tool_call(CHAT_READ, {"limit": 2}),
        final("never"),
        settings_overrides={"admission": AC17_ADMISSION, "budget": AC17_BUDGET},
    )
    harness.reader.hold = asyncio.get_running_loop().create_future()
    try:
        await harness.send()
        await asyncio.wait_for(harness.reader.entered.wait(), 1)
        harness.clock.advance(51.0)
        harness.reader.hold.set_result(None)
        record = await harness.completed()

        assert record.status == "error"
        completed = harness.run_completed()
        assert completed["failure"] == RUN_FAILURE_DEADLINE_RESERVE
        assert len(harness.requests()) == 1
        assert len(harness.session.results) == 1  # the final response was never fetched
        # The held read itself ended as the executor's own timeout (10 s bound).
        assert harness.executor.outcome(f"{record.run_id}/call-1").status == "timeout"
        assert_fallback_sent(harness, record, f"{record.run_id}/call-2")
        assert (completed["turns"], completed["action_calls"]) == (1, 2)
        assert "brain run: delivery reserve reached before the next model turn" in harness.diagnostics
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac17_at_the_total_deadline_nothing_is_sent() -> None:
    """AC17 (R3), deadline: at 60 s or later nothing is sent — the fallback
    records ``skipped:deadline_exceeded`` with 0 executor calls for it, the
    run ends at the scheduler's deadline with the loop's accounting merged
    in, and the model saw exactly 1 request."""

    harness = await activate_loop(
        tool_call(CHAT_READ, {"limit": 2}),
        final("never"),
        settings_overrides={"admission": AC17_ADMISSION, "budget": AC17_BUDGET},
    )
    harness.reader.hold = asyncio.get_running_loop().create_future()
    try:
        await harness.send()
        await asyncio.wait_for(harness.reader.entered.wait(), 1)
        harness.clock.advance(60.0)
        harness.reader.hold.set_result(None)
        record = await harness.completed()

        assert record.status == "timeout"
        assert record.reason == REASON_RUN_DEADLINE
        completed = harness.run_completed()
        assert completed["failure"] == RUN_FAILURE_DEADLINE_RESERVE
        assert len(harness.requests()) == 1
        assert_fallback_skipped(harness, record, FALLBACK_SKIPPED_DEADLINE_EXCEEDED)
        assert harness.executor_calls() == [(CHAT_READ, f"{record.run_id}/call-1")]
        assert harness.executor.outcome(f"{record.run_id}/call-1").status == "timeout"
        assert (completed["turns"], completed["action_calls"]) == (1, 1)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_delivery_entry_reached_at_the_total_deadline_is_skipped_by_name() -> None:
    """R3: at the terminal step an entry reached at or after the total
    deadline is not invoked and records ``skipped:deadline_exceeded``;
    the entry before it keeps its own outcome and nothing is dropped
    silently. The first entry's send holds until the clock passes the
    deadline — an entry that emitted and never confirmed is the executor's
    ``external_unknown``, never a retry — and the second is never invoked."""

    harness = await activate_loop(
        final(FINAL_TEXT),
        settings_overrides={
            "admission": AC17_ADMISSION,
            "budget": {**AC17_BUDGET, "action_seconds": 10},
            "delivery": {
                "mode": "fixed",
                "actions": [
                    {"action": CHAT_WRITE, "text_argument": "text"},
                    {"action": CHAT_WRITE, "text_argument": "text"},
                ],
            },
        },
    )
    harness.sender.hold = asyncio.get_running_loop().create_future()
    try:
        await harness.send()
        await asyncio.wait_for(harness.sender.entered.wait(), 1)
        harness.clock.advance(60.0)
        harness.sender.hold.set_result(None)
        record = await harness.completed()

        completed = harness.run_completed()
        assert [entry["status"] for entry in completed["deliveries"]] == [
            "external_unknown",
            FALLBACK_SKIPPED_DEADLINE_EXCEEDED,
        ]
        assert [entry["call_id"] for entry in completed["deliveries"]] == [
            f"{record.run_id}/call-1",
            None,
        ]
        assert completed["delivery"] == "external_unknown"
        assert harness.sender.invocations == 1
        assert completed["action_calls"] == 1
        assert completed["fallback"] == FALLBACK_NONE
        assert harness.memory() == []
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC18: the fallback that sends nothing, for a named reason
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac18_a_disabled_fallback_sends_nothing_and_says_so() -> None:
    """AC18 (R3): with ``fallback.enabled: false``, AC13's run sends 0
    messages and records ``fallback == "skipped:disabled"``; the run still
    ends ``error`` / ``budget_exhausted`` / ``model_turns`` after 2 requests."""

    harness = await activate_loop(
        *(tool_call(CHAT_READ, {"limit": 3}) for _ in range(3)),
        settings_overrides={"budget": {"model_turns": 2}, "fallback": {"enabled": False}},
    )
    try:
        record = await harness.ask()
        completed = assert_exhausted(harness, record, "model_turns")
        assert len(harness.requests()) == 2
        assert_fallback_skipped(harness, record, FALLBACK_SKIPPED_DISABLED)
        assert (completed["turns"], completed["action_calls"]) == (2, 2)
        assert (
            f"brain fallback: {FALLBACK_SKIPPED_DISABLED} after {RUN_FAILURE_BUDGET_EXHAUSTED}"
            in harness.diagnostics
        )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac18_a_fallback_with_no_applicable_rule_costs_no_executor_call() -> None:
    """AC18 (R3): with the fallback enabled but no ``chat.write`` rule, 0
    messages, ``skipped:not_authorized``, the provider invoked 0 times and
    0 executor calls for the fallback — default deny decided on the
    authorized view of the entry's own scope, never by a refused call."""

    harness = await activate_loop(
        *(tool_call(CHAT_READ, {"limit": 3}) for _ in range(3)),
        settings_overrides={"budget": {"model_turns": 2}},
        grants=(CHAT_READ, USERS_READ, SCREEN_CAPTURE),
    )
    try:
        record = await harness.ask()
        completed = assert_exhausted(harness, record, "model_turns")
        assert_fallback_skipped(harness, record, FALLBACK_SKIPPED_NOT_AUTHORIZED)
        assert harness.executor_calls() == [
            (CHAT_READ, f"{record.run_id}/call-1"),
            (CHAT_READ, f"{record.run_id}/call-2"),
        ]
        assert (completed["turns"], completed["action_calls"]) == (2, 2)
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC19: a queued work dropped stale gets the fallback, never an answer
# --------------------------------------------------------------------------- #


async def hold_one_and_queue_another(enabled: bool) -> tuple[Any, HeldSession]:
    """A run of the viewer held in its model call, a second message queued behind it.

    ``wait_seconds: 5``: the queued work's wait deadline is 5 s after its
    admission, well inside the held call's 30 s bound.
    """

    held = HeldSession(FakeResponse(200, final("late")))
    overrides: dict[str, Any] = {"admission": {"wait_seconds": 5, "total_run_seconds": 120}}
    if not enabled:
        overrides["fallback"] = {"enabled": False}
    harness = await activate_loop(session=held, settings_overrides=overrides)  # type: ignore[arg-type]
    await harness.send("first question", message_id="first")
    await asyncio.wait_for(held.entered.wait(), 1)
    await harness.send("second question", message_id="second")
    await settle()
    assert harness.handle.scheduler.run_records() == {}
    return harness, held


@pytest.mark.asyncio
async def test_ac19_a_stale_drop_delivers_exactly_one_fallback_send_and_no_model_request() -> None:
    """AC19 (R3): a queued work expiring ``wait_seconds`` produces
    ``stale_drop``, 0 model requests for it and exactly 1 fallback send whose
    text is ``fallback.text`` — never the stale question — under the
    brain's principal, with the hook's report riding in the drop's one
    ``brain.run.completed``; the held run is untouched by it."""

    harness, held = await hold_one_and_queue_another(enabled=True)
    try:
        harness.clock.advance(5.0)
        await wait_until(lambda: len(harness.sender.sends) == 1)
        record = await harness.completed(1)

        assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
        assert record.started is False
        assert record.model_calls == 0
        # The fallback send is the drop's one send, on the record and in
        # the trace alike — never left at the 0 the record was written with.
        assert record.sends == 1
        assert record.delivery == "fallback:success"
        assert record.correlation["fallback"] == FALLBACK_SENT
        assert "sends" not in record.correlation
        completed = harness.run_completed()
        assert completed["source_event_id"] == "second"
        assert completed["fallback"] == FALLBACK_SENT
        assert completed["delivery"] == "fallback:success"
        assert completed["sends"] == 1
        assert completed["model_calls"] == 0
        assert completed["turns"] == 0
        assert completed["action_calls"] == 1
        stale_call = f"{REASON_STALE_DROP}/second/call-1"
        assert completed["deliveries"] == [
            {"action": CHAT_WRITE, "call_id": stale_call, "text": True, "status": "success"}
        ]
        (send,) = harness.sender.sends
        assert send["text"] == FALLBACK_TEXT
        assert "second question" not in send["text"]
        assert send["call_id"] == stale_call
        assert send["principal"] == PRINCIPAL
        assert send["destination"] == f"{PLATFORM}/{CHANNEL}/{CHAT_SCOPE}"
        assert harness.executor.outcome(stale_call).status == "success"
        # Exactly the held run's request: the stale work asked the model nothing.
        assert len(held.post_calls) == 1
        assert harness.memory() == []
        assert harness.handle.scheduler.stale_drop_hook_failures == 0

        # The held run then finishes on its own answer, as a second send.
        held.release.set()
        held_record = await harness.completed(2)
        assert held_record.status == "success"
        assert [send["text"] for send in harness.sender.sends] == [FALLBACK_TEXT, "late"]
        assert len(held.post_calls) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ac19_a_stale_drop_with_the_fallback_disabled_sends_nothing() -> None:
    """AC19 (R3): the same drop with ``fallback.enabled: false`` produces 0
    sends, ``fallback == "skipped:disabled"`` in the drop's trace, and still
    exactly one ``stale_drop`` record."""

    harness, held = await hold_one_and_queue_another(enabled=False)
    try:
        harness.clock.advance(5.0)
        record = await harness.completed(1)
        assert (record.status, record.reason) == ("timeout", REASON_STALE_DROP)
        assert record.delivery is None
        assert record.correlation["fallback"] == FALLBACK_SKIPPED_DISABLED
        completed = harness.run_completed()
        assert completed["fallback"] == FALLBACK_SKIPPED_DISABLED
        assert completed["deliveries"] == []
        assert harness.sender.sends == []
        assert harness.sender.invocations == 0
        assert len(held.post_calls) == 1
        assert harness.handle.scheduler.stale_drop_hook_failures == 0
        held.release.set()
        await harness.completed(2)
        assert [send["text"] for send in harness.sender.sends] == ["late"]
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_a_stale_drop_at_a_spent_total_budget_reports_synchronously() -> None:
    """R3: a queued work dropped with 0 s of total budget left gets the
    same fallback policy — ``skipped:deadline_exceeded`` — answered without
    an awaitable, so the scheduler, which would abandon one at once, still
    carries the report in the drop's trace; 0 sends."""

    held = HeldSession(FakeResponse(200, final("late")))
    harness = await activate_loop(
        session=held,  # type: ignore[arg-type]
        settings_overrides={
            "admission": {"wait_seconds": 5, "total_run_seconds": 5},
            "budget": {"model_call_seconds": 4, "delivery_reserve_seconds": 1},
        },
    )
    try:
        await harness.send("first question", message_id="first")
        await asyncio.wait_for(held.entered.wait(), 1)
        await harness.send("second question", message_id="second")
        await settle()
        harness.clock.advance(5.0)
        await wait_until(
            lambda: any(
                record.reason == REASON_STALE_DROP
                for record in harness.handle.scheduler.run_records().values()
            )
        )
        await wait_until(lambda: len(harness.traces("brain.run.completed")) >= 1)
        stale = next(
            trace["payload"]
            for trace in harness.traces("brain.run.completed")
            if trace["payload"]["source_event_id"] == "second"
        )
        assert stale["reason"] == REASON_STALE_DROP
        assert stale["fallback"] == FALLBACK_SKIPPED_DEADLINE_EXCEEDED
        assert stale["deliveries"] == []
        assert harness.sender.sends == []
        assert harness.handle.scheduler.stale_drop_hook_failures == 0
        held.release.set()
    finally:
        await harness.close()
