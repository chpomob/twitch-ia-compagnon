# Phase 3 — full-branch review gate (closing gate before delivery)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`, full diff of the phase 3 campaign against the
phase 2 delivery point.

**Write your report to `docs/campaigns/phase3/scaffolding/gate-report-1.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no other
file.

## Authority

- `docs/campaigns/phase3/spec.md` (R1–R8, AC1–AC42) — the spec under test
- `docs/campaigns/phase3/plan.md` (P1–P26) — what was supposed to be built
- `docs/campaigns/phase3/brief.md` — **the operator's decisions, verbatim**: they are requirements
  (presence pack + clips + viewer memory; moderation with three CONFIGURABLE modes; a continuous "watch" input
  only when requested and configurable; memory with configurable duration / size / file count and an
  eviction that analyses least-used-or-oldest; Kick and YouTube adapters now)
- `docs/research/usage-createurs-assistant-ia-2026.md` — why each workstream exists
- `docs/design-v2.md` §6 "Phase 3+" and §7 (memory as optional modules, volatile bounded core)
- `docs/proxy-protocol.md` — protocol v1, normative
- `docs/campaigns/phase{1,2}/{spec.md,plan.md}` — the delivered contracts that must NOT regress

## Your job

1. **Map every requirement R1–R8 to a NAMED RUNNING TEST** and run the suite yourself
   (`.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`). A requirement with no named test is a finding.
2. **Adversarially attack the phase 3 guarantees**, from the code and from RUNNING tests, starting with the
   operator's decisions:
   - **Moderation**: the three modes (`alert`, `propose`, `act`) are CONFIGURABLE, the shipped default is the
     strictest (`alert`, zero platform effect), permanent bans are never available, every action is audited,
     the rate/target bounds hold, and `model_proposable` is limited to the moderation request. Try to make the
     bot moderate without a rule, bypass a mode, or act silently.
   - **Memory**: bounds per file, in total bytes and in file COUNT are enforced; retention is 30 days by
     default and configurable; the eviction rule (analyse least-used/oldest, then delete) is observable and
     test-pinned; recall honours the caller's rule; recording happens after the terminal delivery and never by
     the model; erasure works per viewer and globally; a memory file never grows unbounded and never exceeds
     the configured count. Look for a path that leaks, never evicts, or loses the "who said what".
   - **Watch**: `watch_tick` is emitted ONLY while a session was explicitly requested; ticks go through the
     trigger engine and admission; cadence and caps are enforced; watch runs act under `brain.watch` and each
     action they use needs its own rule; shutdown stops the ticks; nothing behaves like a background capture.
   - **Clips**: `stream.clip.create` is effect-only, never model-proposed, bounded, with the failure taxonomy;
     no clip is created without an applicable rule.
   - **Presence pack**: the shipped configuration is honest about what is configuration vs what still needs
     code; the end-to-end scenario passes.
   - **Platforms**: Twitch, Kick and YouTube declare capabilities HONESTLY (a platform that cannot clip or poll
     must show the action unbound with a named reason); no platform or vendor literal in `core/`; rate/quota
     handling is real (Kick webhook signature verification; YouTube polling under its daily quota ledger).
   - **Non-regression**: phases 0–2 guarantees (default-deny including reads, bounded admission, terminal
     outcomes, R10 precedence, the two authorized deadline subtractions, AC43's no-provider default, one
     action per turn, no uncertain outcome memorized as confirmed).
   - **Honesty**: no weakened/skipped/xfailed assertion, no swallowed error, no stub validator, no test bent to
     match an implementation. Distinguish a legitimate contract change (with the replacing test named) from a
     weakened assertion.
3. **Report the per-provider integration trials** in `docs/README.md` honestly (what actually ran, what could
   not, and why), and state plainly anything you could not verify.

## Deliverable (write to `docs/campaigns/phase3/scaffolding/gate-report-1.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **REQUIREMENT MAP**: R1–R8 → named running test + AC coverage
- **CHECKS**: one line each, PASS/FAIL/NOT-VERIFIABLE with the command used
- **FINDINGS**: only with a concrete counterexample or an executed reproduction, each with `file:line`
- **NOT VERIFIED**: stated plainly

This gate closes phase 3. If everything you can check passes, say APPROVE plainly.
