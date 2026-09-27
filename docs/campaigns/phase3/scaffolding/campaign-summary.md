# Phase 3 — campaign summary (final)

**Status: COMPLETE — full-branch closing gate APPROVED on 2026-09-27 (fifth pass).**

## Delivery

- `main` at `fb7f99a`, clean tree, relative to the phase 2 delivery `2165383`.
- **26 plan steps (P1–P26) + 6 gate fix steps** (P27F1/F2/F3, P28F1, P29F1, P30F1) — one atomic commit each,
  every one reviewed by the adversarial loop (Claude DEV + Codex REVIEW) before landing.
- **42 commits, 113 files, +42,131 lines**; **2,460 tests pass, 18 skip** (phase 2 close: 1,891/12).
- **17 modules** (from 12): `clips`, `kick`, `moderation`, `viewer_memory`, `watch`, `youtube` are new.
- Spec: `docs/campaigns/phase3/spec.md` (R1–R8 / AC1–AC42). Plan: `docs/campaigns/phase3/plan.md`.
  Brief with the operator's verbatim decisions: `docs/campaigns/phase3/brief.md`.

## What phase 3 added (the operator's six workstreams)

1. **Presence pack** — a configurable persona and ordered **routes** in the brain (match on event kind,
   leading command token, trusted audience → extra instructions + delivery list) plus **community notices**
   ingested by the platform modules; delivered as shipped configuration (`presence.yaml.example`) with the
   parts that still need code listed honestly.
2. **Live clips** — `stream.clip.create`, an effect-only write served by a new `clips` module through a `clip`
   service each platform publishes; never model-proposed.
3. **Viewer memory** — `modules/viewer_memory`: one JSON file per viewer, bounded per file / in total bytes /
   in file COUNT, retention **30 days** by default, all three configurable, eviction by an explicit
   test-pinned least-used/oldest analysis, `memory.recall` (read) and `memory.record` (written by the brain
   after the terminal delivery, never by the model), an offline erasure command (`python -m modules.viewer_memory`)
   and `!forgetme`.
4. **Configurable moderation** — `moderation.request` with the three modes `alert` / `propose` / `act`
   (**`alert` shipped by default**, zero platform effect), permanent bans never available, strict rules, audit
   of every action, approval/rejection commands with expiry and per-channel overrides.
5. **Watch input** — `modules/watch` emits `watch_tick` only while a session was explicitly requested, at a
   configured cadence with caps; ticks go through the trigger engine and admission and act under their own
   `brain.watch` principal, so every action a watch run uses needs its own rule; never a background capture.
6. **Kick and YouTube adapters** — Kick via signed webhooks (chat, notices, `timeout`-only moderation) and
   YouTube via OAuth refresh + live-chat polling under a daily quota ledger (chat, notices, delete/timeout);
   neither publishes a clip or poll service, and the capability view says so with a named reason.

## Review history (what the escalations actually caught)

| Gate pass | Verdict | Findings |
|---|---|---|
| 1 | REQUEST_CHANGES | **7** — F1 (major) empty-text community notices never reached the presence routes (follows/Kick notices/YouTube members were rejected as malformed); F2 (major) unsupported poll capabilities had no AC39 reason; F3 (major) failed startup evictions silently violated the memory bounds; F4–F7 (minor) fictitious clip platform, lost `model_proposable` declaration, self-contradictory AC13, non-empty core-literal scan |
| 2 | REQUEST_CHANGES | 6 of 7 RESOLVED, **no new defect**; F3 partially (undeletable oversized file not counted; silent failed deletion) |
| 3 | REQUEST_CHANGES | F3 fully corrected; **F8 (major) introduced by the fix**: failure reports unbounded before supervision (3,630 → 223,378 characters at 32/256/2048 files) and file identity lost on truncation |
| 4 | REQUEST_CHANGES | F8's original defects corrected; one measured **residual**: batches materialized eagerly (32 failures → 32 records before the first emit) |
| 5 | **APPROVE** | F8 RESOLVED — exactly 6 records constructed before the first emit, peak bounded by the batch size |

## Operator limits declared, not hidden (gate 5's explicit note)

The **live trials are NOT VERIFIED**: `tests/test_phase3_trials.py` contains six opt-in trial nodes (platform
clips, live moderation, community notices, Kick, YouTube, screen watch) that were collected and **skipped**
because their credentials/environment variables were unset. `docs/README.md` records which trials ran, which
did not, and why. The one real integration trial executed earlier in the project (obs-websocket v5 scenes,
`docs/campaigns/phase2/scaffolding/obs-trial-record.md`) remains the model for what a real trial looks like.

## Durable scaffolding (inside the repository)

`run_campaign_phase3.py`, `steps/P1..P26-spec.md` + `P27F1/F2/F3`, `P28F1`, `P29F1`, `P30F1`, `gate-prompt-1..5.md`,
`gate-report-1..5.md`, `campaign.log`, plus the phase-3 spec, plan and brief.
