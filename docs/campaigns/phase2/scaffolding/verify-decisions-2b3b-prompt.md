You are the independent VERIFIER of two arbiter decisions recorded in the phase 2 authority documents.
Answer with EXACTLY one JSON object, no prose, no markdown fence.

Read: `docs/campaigns/phase2/spec.md` (new `AC43`, the environment-facts section, decision 2),
`docs/campaigns/phase2/plan.md` (steps `P19`, `P20`), `docs/design-v2.md` (§4.4, §6 Phase 2), and the
broader context (`docs/campaigns/phase1/spec.md`, the phase 1 delivery/capability model in `core/`).

THE TWO DECISIONS
- **2b** — NO speech provider is shipped as a default: the speech and transcription endpoint settings are
  empty in the shipped profiles and examples; while empty (or unreachable at prepare) `audio.speak` is
  unbound, the runtime names it with a value-free not-ready reason, no request is attempted, and a chat-only
  or freshly installed profile starts clean.
- **3b** — a REAL scene-provider integration trial is performed against a provider installed on the machine
  (obs-websocket protocol 5 over the `websocket` kind); "not run / closed port" is recorded only if the trial
  genuinely could not run.

CHECK:
- Y1: is 2b stated as a normative criterion (`AC43`) that is testable, and is it CONSISTENT with the design
  (§4.4 "discovered but not ready", the capability view) and with phase 1's capability/probe rules — no
  contradiction with the earlier decision 2 text or with any other AC?
- Y2: does the plan (`P19` tests, `P20` trial records) carry 2b and 3b so the implementation cannot silently
  ignore them? Name any place that still assumes a default endpoint or a "not run" scene trial.
- Y3: any contradiction, ambiguity or unintended weakening introduced by these two decisions? State plainly if
  something is still unclear.

Output: {"results": [{"id": "Y1", "status": "RESOLVED|PARTIAL|NOT_RESOLVED", "evidence": "<quote/line>", "comment": "<one line>"}, ... Y1..Y3], "verdict": "APPROVE|REJECT"}
APPROVE if all three are RESOLVED.
