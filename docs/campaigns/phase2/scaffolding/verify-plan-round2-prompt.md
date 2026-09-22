You are the independent VERIFIER of a plan revision. Answer with EXACTLY one JSON object, no prose, no fences.

Target: `plan.md` on the current branch (`plan/phase2-audio/2`). Compare it against the spec
`docs/campaigns/phase2/spec.md` (authority; note R3, R10, AC38-AC41) and the real code it cites
(`core/actions.py`, `core/context.py` or the module that builds `ModuleServices`, `core/runtime.py`).
Use read-only commands freely.

THE THREE FINDINGS THE REVISION HAD TO RESOLVE (round 2):
- P1 (blocker, step P11) "The transcription deadline path cannot preserve the required successful capture."
  R3 requires a transcription failure to leave the observation `success` with its `audio_ref`; the old text
  left it `cancelled` (or a `success` stamped at/after `expiry`, which step P3 discards) when the call budget
  expired first.
- P2 (major, step P13) "The publication guard does not preserve compatibility with contexts lacking a service
  registry." The guard tested the FACADE's presence, but a default `RuntimeContext` carries the facade while
  the underlying registry is absent, so publication was attempted and activation failed.
- P3 (major, step P4) "The accepted service interface omits enumeration required by the poll consumer."
  `_require_surface` accepted a two-method (`publish`/`resolve`) collaborator while step P16 unconditionally
  calls `entries()`.

CHECK, one result entry each:
- V1: is P1 settled by a stated, testable boundary that preserves R3 (capture stays success with its audio_ref)
  and does not contradict R10 or the "a late success is never adopted" rule? Check the arithmetic of the stated
  window against the executor's adoption rule, and that a test pins it.
- V2: is P2 settled by guarding the UNDERLYING REGISTRY (not the facade), keeping the caller table's claim true,
  with a test that activation through a default context publishes nothing and does not fail?
- V3: is P3 settled by ONE consistent interface that both `_require_surface` and the consumer honour, with a
  test exercising the accepted collaborator through `stream_control.prepare`?
- V4: does the revision INTRODUCE a new contradiction with the spec or with the plan's own earlier resolutions
  (the five round-1 findings P1-P5 are supposedly kept)?

Output: {"results": [{"id": "V1", "status": "RESOLVED|PARTIAL|NOT_RESOLVED", "evidence": "<quote/line>", "comment": "<one line>"}, ... V1..V4], "verdict": "APPROVE|REJECT"}
APPROVE only if V1..V4 are all RESOLVED.
