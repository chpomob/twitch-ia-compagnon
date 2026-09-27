# Phase 3 — full-branch closing gate, SECOND pass (after the F1–F7 fix round)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Gate 1
(`docs/campaigns/phase3/scaffolding/gate-report-1.md`) returned **REQUEST_CHANGES** with seven findings
(3 major, 4 minor) and runnable reproductions; three fix steps have since landed.

**Write your report to `docs/campaigns/phase3/scaffolding/gate-report-2.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no other
file.

## Your job

1. **Verify each finding of gate 1**, from the code AND a RUNNING test, citing `file:line` and the executed test
   name. Status per finding: `RESOLVED`, `PARTIALLY RESOLVED` or `NOT RESOLVED`.
   - **F1 (major)** — ordinary empty-text community notices (Twitch follows, Kick notices, YouTube
     members/gifts) never reached the presence routes because the message helper required non-blank text.
     Required: a notice carrying a KIND is accepted with empty text, a genuine blank TEXT event stays
     malformed, and the presence routes fire. Re-run the gate's reproduction.
   - **F2 (major)** — unsupported poll capabilities (Kick, YouTube) had no AC39 named reason. Required: the
     action is unbound with a value-free named reason on those platforms, with the tests and the README row
     updated consistently.
   - **F3 (major)** — failed startup evictions silently violated the memory file bounds. Required: the
     configured bounds (per file, total bytes, file count) hold across a failing eviction, and the failure is
     visible, never silent.
   - **F4 (minor)** — the clips module accepted a scheduler service as a fictitious clip platform, and the
     presence test accepted it. Required: only a real clip platform is accepted; otherwise the action is
     unbound with a reason.
   - **F5 (minor)** — the protocol/agent declaration lost `model_proposable`. Required: either carried and
     tested across the wire, or explicitly documented as local-only with the reason — no claim/code
     disagreement.
   - **F6 (minor)** — AC13's literal substring prohibition contradicted its own hash example. Required:
     self-consistent criterion and test, changed explicitly.
   - **F7 (minor)** — the broad core-literal scan was not empty. Required: literals removed, or the scan
     narrowed with a documented falsifiable exclusion — never dropped.
2. **Re-run the whole checklist of gate 1** (requirement map R1–R8 → named running tests; the operator's six
   workstreams — presence pack, clips, viewer memory with its configurability and analysis-based eviction,
   the three CONFIGURABLE moderation modes with the strictest default and no permanent bans, the requested-only
   configurable watch input, honest Kick/YouTube capabilities; protocol v1; phases 0–2 non-regression —
   default-deny, bounded admission, terminal outcomes, R10 precedence, the two authorized deadline
   subtractions, AC43's no-provider default; honesty of the assertion changes; provider trials).
3. **Hunt for NEW defects introduced by the fix round** — especially any assertion changed to match an
   implementation, any notice path that now admits genuinely malformed work, and any new unbounded growth.

## Deliverable (write to `docs/campaigns/phase3/scaffolding/gate-report-2.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **PREVIOUS FINDINGS**: F1–F7 with status, `file:line`, executed test, and the outcome of re-running the
  reproductions
- **CHECKS**: one line each, PASS/FAIL/NOT-VERIFIABLE with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 3. If everything you can check passes, say APPROVE plainly.
