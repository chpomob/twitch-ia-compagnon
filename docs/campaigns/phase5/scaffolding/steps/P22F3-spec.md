# Step P22F3 — a cue is never recorded as the speech of the viewer it names

Gate fix step of Phase 5. It is **not** one of the plan's numbered steps: it answers finding **F3**
of the phase's closing review (`docs/campaigns/phase5/full-branch-review.md`), which refused approval of
the assembled branch.

### F3 — MEDIUM: a cue about a viewer is recorded as that viewer's speech

Locations: `modules/brain/__init__.py:3277` (memory eligibility), `modules/brain/__init__.py:3368` (note construction); conflicting guide claim at `docs/automation-bridge.md:258–260` and its translation.

The guide says viewer_memory acts on ordinary messages only. P11's audit explicitly warned that the brain's post-run recording is separate from viewer_memory's bus handler. The current eligibility check excludes reserved colon identities, so anonymous instance cues are safe, but a valid cue with user_id enters memory.record with the operator's cue text as viewer_text. A successful reply is also attached as a confirmed viewer exchange.

Executed through the real bridge normalizer, brain and executor, with a recording memory provider (no persistent files written):

~~~python
import asyncio
from dataclasses import replace
import test_brain as b
from modules.streamerbot import cue_event

async def main():
    factory = b.owned_rules(
        replace(b.MEMORY_RECORD_PROPOSABLE_SPEC, model_proposable=False),
        rules=[b.BRAIN_GRANT, b.grant_to(b.MEMORY_RECORD, b.PRINCIPAL)],
    )
    h = await b.activate_with(
        b.FakeResponse(200, b.completion("Back soon.")), context_factory=factory)
    try:
        p = b.chat_payload("")
        envelope = dict(
            cue_id="cue-memory-1", platform=p["platform"], channel_id=p["channel_id"],
            user_id=p["author"]["id"], user_name="Viewer", text="!brb")
        e = cue_event({
            "event": {"source": "Custom", "type": "Event"},
            "data": {"args": envelope},
        }, {})
        await h.bus.publish(e["type"], e["payload"], e["metadata"])
        (record,) = await h.completed()
        print(e["payload"]["kind"], record.status)
        calls = factory.providers[b.MEMORY_RECORD].calls
        print(calls)
        assert calls == [{
            "viewer_text": "!brb", "delivery": "confirmed", "reply_text": "Back soon.",
        }]
    finally:
        await h.close()

asyncio.run(main())
~~~

Observed `cue success` and exactly the asserted memory-record call. This attributes automation instructions to a viewer and contradicts the operator guide. Resolve the P11 finding by excluding cues from automatic viewer-memory recording, with an end-to-end regression for both user-bearing and instance-author cues. If attribution is deliberately retained, it needs an explicit operator decision and corrected documentation; the current claim cannot stand.

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


**Resolution: cues are excluded from automatic viewer-memory recording.** This restores what the phase's
own operator guide already claims (`docs/automation-bridge.md`: viewer memory acts on ordinary messages
only) and closes the warning P11's consumer audit raised.

A cue is an instruction the operator's automation routes to the companion; attributing its text to the
viewer it happens to name fabricates an utterance. Exclude cues from the post-run memory recording for
**both** the user-bearing and the instance-author case, and keep the reply from being attached as a
confirmed viewer exchange.

If you find that exclusion cannot be expressed without also suppressing legitimate recording of ordinary
messages in the same run, stop and report it: the operator's decision would be needed, not yours. This
step implements the exclusion; it does not retain attribution.

## Files

[`modules/brain/__init__.py`, `tests/test_brain.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing
it.

## Tests

In `tests/test_brain.py`: an end-to-end regression in the finding's shape — a cue carrying a `user_id`
drives a successful run and produces **zero** `memory.record` calls; a cue with the instance's anonymous
identity produces zero; an ordinary `chat` message in the same harness still produces exactly one
`memory.record` call with the viewer's own text and its delivery outcome, so the exclusion is scoped to
cues and nothing else moved.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,600 passed / 26 skipped, the count when this gate ran.

## Dependencies

[P22FP22F2] — the previous gate fix lands first; all its dependencies are already merged into `main`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P22F3 — ...`). The scope MUST be `phase5`.
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
