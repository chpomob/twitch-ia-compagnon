"""Acted per-run budgets in the agentic loop (R3; AC13 loop part, AC14, AC15, AC16).

Every budget is finite, validated and acted at its own boundary: model
turns before another model call, repeated proposals and action calls
before a proposal is executed, cumulative tokens on every reply, the model
call's time bound on the injected clock, an action's time bound as the
executor's own ``timeout`` observation fed back to the model. Exceeding a
budget ends the run ``error`` with ``failure: budget_exhausted`` and
``budget: <name>``; a model call over ``model_call_seconds`` ends it
``timeout``. The fallback delivery those failures trigger is the next
step's; here ``fallback`` reads ``none``.

The harness is the agentic-loop one: the shipped brain, the real executor,
scheduler and store, read doubles bound in the test. Nothing sleeps: the
clock is injected and every wait is a bounded number of bare loop turns.
"""

from __future__ import annotations

import asyncio

import pytest

from core.actions import ERROR_TIMED_OUT
from core.contracts import RUN_FAILURE_BUDGET_EXHAUSTED
from conftest import FakeResponse, HeldSession, final, tool_call
from modules.brain import DELIVERY_NOT_ATTEMPTED, FALLBACK_NONE
from test_agentic_loop import (
    CHAT_READ,
    CHAT_WRITE,
    FINAL_TEXT,
    SCREEN_CAPTURE,
    activate_loop,
    observation_envelope,
    tool_messages,
)


def assert_exhausted(harness, record, budget: str) -> dict:
    """The record and its trace read ``error`` / ``budget_exhausted`` / *budget*."""

    assert record.status == "error"
    assert record.delivery == DELIVERY_NOT_ATTEMPTED
    completed = harness.run_completed()
    assert completed["status"] == "error"
    assert completed["failure"] == RUN_FAILURE_BUDGET_EXHAUSTED
    assert completed["budget"] == budget
    assert completed["fallback"] == FALLBACK_NONE
    assert completed["deliveries"] == []
    return completed


# --------------------------------------------------------------------------- #
# AC13 (loop part): model turns
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac13_the_model_turn_budget_stops_the_loop_after_exactly_that_many_requests() -> None:
    """AC13 (R3), loop part: with ``model_turns: 2`` and a model proposing
    ``chat.read`` on every turn, the run ends ``error``, ``failure:
    budget_exhausted``, ``budget: model_turns`` after exactly 2 model
    requests — both proposals executed, no third request built."""

    harness = await activate_loop(
        *(tool_call(CHAT_READ, {"limit": 3}) for _ in range(3)),
        settings_overrides={"budget": {"model_turns": 2}},
    )
    try:
        record = await harness.ask()
        completed = assert_exhausted(harness, record, "model_turns")
        assert len(harness.requests()) == 2
        assert len(harness.reader.calls) == 2
        assert (completed["turns"], completed["action_calls"]) == (2, 2)
        assert harness.sender.sends == []
        assert len(harness.session.results) == 1  # the third body was never asked for
        assert "brain run: budget exhausted: model_turns" in harness.diagnostics
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
    ``budget: max_repeated_actions``. A third ``chat.read`` with a different
    ``limit`` is executed, and the run goes on to its final response."""

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
        assert (completed["turns"], completed["action_calls"]) == (3, 2)
        assert repeated.sender.sends == []
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
        assert varied.sender.sends[0]["call_id"] == f"{record.run_id}/call-4"
    finally:
        await varied.close()


@pytest.mark.asyncio
async def test_the_action_call_budget_stops_a_proposal_before_the_executor() -> None:
    """R3: a proposal with no action call left is not executed — the run
    ends ``error``, ``budget: max_action_calls`` with exactly
    ``max_action_calls`` executor calls made; with ``max_repeated_actions``
    wide enough the repeated-action budget is not what stops it."""

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
    without ``usage`` the recorded ``tokens_estimated`` is ``true``."""

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
        assert reported.sender.sends == []
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
        assert estimated.sender.sends == []
    finally:
        await estimated.close()


@pytest.mark.asyncio
async def test_tokens_accumulate_over_the_turns_of_one_run() -> None:
    """R3: ``max_tokens`` is cumulative input + output per run — two replies
    each under the bound but over it together end the run on the second,
    with the sum recorded; the request's output cap is what the run has
    left."""

    harness = await activate_loop(
        tool_call(CHAT_READ, {"limit": 1}, usage={"total_tokens": 600}),
        tool_call(CHAT_READ, {"limit": 2}, usage={"total_tokens": 600}),
        final("never"),
        settings_overrides={"budget": {"max_tokens": 1000}},
        grants=(CHAT_WRITE,),
    )
    try:
        record = await harness.ask()
        completed = assert_exhausted(harness, record, "max_tokens")
        assert completed["tokens"] == 1200
        assert completed["tokens_estimated"] is False
        assert len(harness.requests()) == 2
        first, second = harness.requests()
        assert first["max_tokens"] > 600 > second["max_tokens"] > 0
    finally:
        await harness.close()


# --------------------------------------------------------------------------- #
# AC16: time bounds on the injected clock
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ac16_a_model_request_held_past_model_call_seconds_ends_the_run_timeout() -> None:
    """AC16 (R3), model side: advancing the injected clock 31 s while a
    model request is held ends the run ``timeout`` with exactly 1 model
    request, the held call still counted."""

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
    finally:
        await harness.close()
