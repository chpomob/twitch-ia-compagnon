Read these three files only:
1. `docs/campaigns/phase2/spec.md` — sections "Environment facts", "Decisions this spec settles" item 2, and `AC43`.
2. `docs/campaigns/phase2/plan.md` — steps `P19` and `P20` only.
3. `docs/design-v2.md` — section 4.4 (the seven starter modules), the sentence about a module whose external
   dependency is absent ("discovered but not ready") and about visible capabilities.

Two arbiter decisions were recorded:
- 2b: NO speech provider default. Speech and transcription endpoints are empty in the shipped profiles and
  examples; while empty (or unreachable at prepare) `audio.speak` is unbound, the runtime names it with a
  value-free not-ready reason, no request is attempted, and a chat-only or freshly installed profile starts.
- 3b: a REAL scene-provider integration trial against a provider installed on the machine (obs-websocket
  protocol 5 over the `websocket` kind); "not run" is recorded only if the trial genuinely could not run.

Answer with EXACTLY this JSON object and NOTHING else — no tables, no prose, no markdown fence:

{"results":[{"id":"Y1","status":"RESOLVED|PARTIAL|NOT_RESOLVED","evidence":"<short quote>"},{"id":"Y2","status":"...","evidence":"..."},{"id":"Y3","status":"...","evidence":"..."}],"verdict":"APPROVE|REJECT"}

Y1 = is 2b stated as a testable normative criterion (AC43) consistent with design §4.4 and with phase 1's
capability/probe rules, with no contradiction elsewhere in the spec?
Y2 = do plan steps P19 and P20 carry 2b and 3b so the implementation cannot ignore them?
Y3 = any contradiction, ambiguity or unintended weakening introduced by these two decisions?
APPROVE only if Y1 and Y2 are RESOLVED and Y3 finds nothing.
