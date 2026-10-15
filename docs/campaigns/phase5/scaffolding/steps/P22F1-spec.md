# Step P22F1 — the ownership guard covers every public module publication route

Gate fix step of Phase 5. It is **not** one of the plan's numbered steps: it answers finding **F1**
of the phase's closing review (`docs/campaigns/phase5/full-branch-review.md`), which refused approval of
the assembled branch.

### F1 — HIGH: public supervision publication bypasses input ownership

Locations: `core/runtime.py:1128` (ModuleSupervision.emit), `core/runtime.py:1137` (record_and_emit), and `core/runtime.py:1503` (for_module wiring).

Only bus/triggers/chat receive the shared ownership guard. ModuleSupervision delegates both publication methods to Supervision, whose publisher holds the unguarded bus. A module needs no private attribute or forged source to bypass the new rule. Its ordinary public supervision surface accepts channel.chat.message.

Executed reproduction:

~~~python
import asyncio
from test_input_ownership import _guarded_runtime, _intruder, _chat_event

async def main():
    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)  # owns chat/y; native owns chat/x
    seen = []
    runtime.bus.subscribe("channel.chat.message", lambda event: seen.append(event))
    event = _chat_event("x")
    await context.bus.publish(event["type"], event["payload"], event["metadata"])
    print(len(seen), context.supervision.snapshot()["input_ownership_rejections"])
    await context.supervision.emit(
        event["type"], event["payload"], metadata=event["metadata"])
    print(len(seen), context.supervision.snapshot()["input_ownership_rejections"])
    await context.supervision.record_and_emit(
        lambda: None, event["type"], event["payload"], metadata=event["metadata"])
    print(len(seen), context.supervision.snapshot()["input_ownership_rejections"])
    assert len(seen) == 2

asyncio.run(main())
~~~

Observed `0 1`, then `1 1`, then `2 1`. The two bypassed publications reach the subscriber, neither increments the rejection counter nor adds its rejection diagnostic. This violates R3's all-subscriber guarantee even though AC13's bus-only example passes. Close every public module publication route over the same guard, or forbid input publication through supervision; cover both methods.

## Authority

Read these, do not re-derive them:
- The closing gate's report — the finding above is quoted from it verbatim, with its executed
  reproduction: `docs/campaigns/phase5/full-branch-review.md`.
- Approved plan: `docs/campaigns/phase5/plan.md` (deviations D1–D11 as recorded in
  `docs/campaigns/phase5/scaffolding/plan-amendments.md`).
- Approved specification: `docs/campaigns/phase5/spec.md`.
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`.
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules).
Working tree at `main`.

The gate's finding is the specification of the defect; the required resolution below is the behaviour
to implement. Do not re-open the finding's premises and do not widen the change beyond it.

## Required resolution


The guard must cover **every public publication route a module can reach** — not the bus alone. The
preferred shape is one guard, one implementation: the rejection decision that `ModuleContext.bus`
already applies is applied by `ModuleContext.supervision.emit` and `record_and_emit` as well, so no
route publishes an input-family event it does not own. If the existing structure makes that
impossible without changing a published signature, refuse input-family publication through the
supervision surface with the same refusal semantics (drop, count, diagnose) and say so in your report.

Requirements:
- the finding's reproduction must end with **0** bypassed publications instead of 2, and the
  `input_ownership_rejections` counter must increment once per attempt, with a diagnostic per rejection
  (same shape as the bus route's);
- a module publishing **its own** family through any route must still work unchanged — closing the hole
  must not close legitimate publication;
- the guard stays a single implementation: do not add a second, divergent copy of the ownership rule;
- the whole-observation rule of R3 stands: no subscriber sees a rejection, and nothing is delivered
  half-way.

## Files

[`core/runtime.py`, `tests/test_input_ownership.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing
it.

## Tests

In `tests/test_input_ownership.py`: the finding's counterexample, turned into a regression test — through
a context that owns (`chat`, y) and a native owner of (`chat`, x), publishing a `chat`/x event via
`context.bus.publish`, `context.supervision.emit` and `context.supervision.record_and_emit` leaves the
subscriber count unchanged and the rejection counter at 3; a module publishing its own pair is delivered
exactly once through each of the three routes; `record_and_emit` still records its record when it
rejects the publication (state the chosen behaviour explicitly in the test).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,600 passed / 26 skipped, the count when this gate ran.

## Dependencies

[P22FP21] — the previous gate fix lands first; all its dependencies are already merged into `main`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P22F1 — ...`). The scope MUST be `phase5`.
- Keep every phase-0 to phase-4 guarantee in force, and every phase-5 decision: the bridge stays
  optional in every case, its events may replace the basic inputs but never run in parallel with them,
  and an execution the instance cannot confirm is never reported as a success.
- Never weaken, skip, xfail or delete an existing assertion to make the suite pass. If an existing
  test encodes the behaviour this finding calls a defect, replace it and cite the finding id and the
  requirement it restores in the test docstring.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do
  because this fix step did not cover it.
