You are the spec VERIFIER. A challenge pass raised four findings against the phase 3 specification; the author
then revised it. Decide, for EACH finding, whether the CURRENT revision resolves it.

Target: `docs/campaigns/phase3/scaffolding/spec-v1.md` (copy of the revised spec, in this repository).
Context if needed: `docs/campaigns/phase3/brief.md` (the authority: the operator's decisions, verbatim),
`docs/design-v2.md`, `docs/campaigns/phase2/spec.md` (the delivered contracts).

The four findings (verbatim):
- S1 (blocker) The memory note schema contradicts the required failed-delivery record.
- S2 (blocker) The recall byte limit cannot always accommodate the mandatory response fields.
- S3 (major) The clips module's required policy is declared but never specified or tested.
- S4 (minor) The memory recording budget criterion does not distinguish used calls from remaining calls.

For each, judge only from the text of the revision: is the point now settled concretely (a requirement or
acceptance criterion that states the rule, a named test, a bounded arithmetic that closes the contradiction), or
still open/hand-waved? A finding is RESOLVED only if the revision states the resolution explicitly.

Answer with EXACTLY one JSON object, no prose, no markdown fence:
{"results":[{"id":"S1","status":"RESOLVED|PARTIAL|NOT_RESOLVED","evidence":"<quote or line>","comment":"<one line>"}, ... S1..S4], "verdict":"APPROVE|REJECT"}
APPROVE only if all four are RESOLVED.
