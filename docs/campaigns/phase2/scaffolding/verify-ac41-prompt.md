You are the independent VERIFIER. A verifier previously rejected a plan revision on two points (V1, V4).
An arbiter ruling amended the specification and the plan to settle them. Decide whether they are settled NOW.
Answer with EXACTLY one JSON object, no prose, no markdown fence.

Read: `docs/campaigns/phase2/spec.md` (now version 1.2: rewritten `AC41`, new `AC42`, R3, R10),
`plan.md` (decision 1, P11, P20, P21, the round-3 table), `core/actions.py` (`_observe`, `_run_provider`)
and the phase 1 tests pinning the deadline boundary.

THE TWO POINTS
- V4: the plan exempted a SECOND deadline subtraction ("R3's two reserves") while `AC41` authorized only the
  `capture_too_long` admission check — an unauthorized exception.
- V1: a transcription request that wakes late can still be stamped at or after `expiry` by
  `core/actions.py::_observe`, in which case the `success` is discarded and R3's guarantee ("the capture stays
  `success` with its `audio_ref`") is lost.

THE ARBITER RULING THAT WAS ENCODED
`AC41` must authorize EXACTLY TWO named subtractions, each with its purpose and its bound: R3's
`capture_too_long` admission check, and the optional transcription window of `audio.capture` — whose only
purpose is that the capture's `success` record (with its `audio_ref`) is stamped BEFORE `expiry` — with the
recorder's kill unchanged at `expiry` and no other action affected. Plus a new criterion: when the
transcription does not finish inside its window (including a delayed wake), the observation stays `success`
with its `audio_ref`, stamped strictly before `expiry`, `transcription_status` reporting the timeout, with
both tests.

CHECK, one result entry each:
- W1: does the AMENDED `AC41` now authorize exactly two named subtractions, each with purpose and bound, and
  is the phase-1 no-lead guarantee still enforced for every other action? Quote the new wording.
- W2: does the new criterion (next free AC id) state the late-wake case as testable, with both tests, and is
  it CONSISTENT with R3, R10 and the executor's actual stamping (`_observe`)?
- W3: is `plan.md` now free of the unauthorized-exception wording, and does P11's window arithmetic match the
  amended `AC41`/the new criterion exactly (including what happens when the window is exhausted at entry)?
- W4: any remaining contradiction, or any place where the plan still relies on a subtraction the spec does not
  authorize? State plainly if a residual risk is honestly DECLARED as a limit rather than claimed resolved.

Output: {"results": [{"id": "W1", "status": "RESOLVED|PARTIAL|NOT_RESOLVED", "evidence": "<quote/line>", "comment": "<one line>"}, ... W1..W4], "verdict": "APPROVE|REJECT"}
APPROVE only if W1..W4 are all RESOLVED.
