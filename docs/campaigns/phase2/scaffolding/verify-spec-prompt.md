You are the spec VERIFIER. A previous challenge pass raised five findings against the phase 2 specification.
The author then revised it. Decide, for EACH finding, whether the CURRENT revision resolves it.

Target file to read: `docs/campaigns/phase2/scaffolding/spec-v1.md` (inside this repository).
Useful context: `docs/campaigns/phase2/brief.md` and `docs/design-v2.md` (section 6 = phase 2 authority).

The five findings (verbatim):
- S1 (major) Poll tracking has no global retention bound.
- S2 (major) Exact partial playback progress is required without an observable progress contract.
- S3 (major) The websocket scene provider has no identified wire protocol.
- S4 (minor) Speech readiness is undefined when the synthesis probe is disabled.
- S5 (minor) Poll creation has no specified outcome for HTTP 5xx responses.

For each finding, judge only from the text of the revision: does a requirement or acceptance criterion now
settle it concretely (bound, contract, named protocol, stated outcome), or is it still open/hand-waved?
A finding is RESOLVED only if the revision states the resolution explicitly, not merely implies it.

Answer with EXACTLY one JSON object, no prose, no markdown fence:
{"results": [{"id": "S1", "status": "RESOLVED|PARTIAL|NOT_RESOLVED", "evidence": "<quote or line reference from the spec>", "comment": "<one line>"}, ... for S1..S5], "verdict": "APPROVE|REJECT"}

Verdict APPROVE only if all five are RESOLVED.
