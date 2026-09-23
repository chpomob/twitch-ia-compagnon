# Phase 2 — campaign summary (final)

**Status: COMPLETE — full-branch gate APPROVED on 2026-09-23.**

## Delivery

- `main` at `64cdb71`, clean tree, relative to the phase 1 delivery `7e26038`.
- **21 plan steps (P1–P21) + 2 gate fix rounds (P22F1, P22F2)** — one atomic commit each, every one reviewed
  by the adversarial loop (Claude DEV + Codex REVIEW) before landing.
- **1891 tests pass, 12 skip** with the project virtualenv (`.venv/bin/python -m pytest`); 1363/6 at the
  phase 1 close.
- Spec: `docs/campaigns/phase2/spec.md` (v1.2+, R1–R10 / AC1–AC43). Plan: `docs/campaigns/phase2/plan.md`.

## What phase 2 added

Audio output (`audio.speak`, `audio.play`, playback-completed semantics, WAV-complete playback), audio input
(`audio.capture`: bounded 16 kHz mono segments, optional dated transcription with its window), stream control
(`stream.scene.set` through a `none`/`scripted`/`websocket` provider boundary, `stream.poll.create` through a
bounded runtime service registry with reconciliation), audio references over protocol v1, the `audio` model
capability and its prepare-time probe, the delivery list extended to audio entries, profiles and packaging,
degraded/chat-only startup, and the per-provider trial records.

## Review history (what the adversarial gates actually caught)

| Gate | Verdict | Findings |
|---|---|---|
| Full-branch gate 1 | REQUEST_CHANGES | F1 (major) unavailable actions answered `error no_provider` instead of `refused provider_not_ready` with zero invocations, **and the tests accepted it**; F2 (major) disabling the probe made an **empty** endpoint ready; F3 (moderate) empty and unreachable shared one not-ready reason; F4 (moderate) the README over-promised the stalled-wake bound; F5 (moderate) the tree violated the declared scope/allowlist |
| Full-branch gate 2 | **APPROVE** | F1–F5 all RESOLVED, no new finding |

The gate's executed counterexamples (`X1`..`X4` in `gate-report-1.md`) were turned into running tests by
`P22F1`; the plan-stage residuals are recorded in `plan-residuals.md` and were honoured by the implementation.

## Operator environment and real integration evidence

- **obs-websocket v5 trial actually run** (not skipped): OBS Studio 30.0.2.1, websocket 5.3.4,
  `GetSceneList` → `CreateScene` → `SetCurrentProgramScene` → **read-back confirmation** all verified against
  the real application. Evidence: `obs-trial-record.md`, probe: `obs-trial-probe.py`, credentials outside the
  repository (`~/.hermes/.env`).
- No speech provider ships as a default (arbiter decision, AC43): the endpoints are empty out of the box and
  the action is named not-ready with a value-free reason.

## Durable scaffolding (inside the repository, survives a reboot)

`run_campaign_phase2.py` (runner: Claude-TUI DEV + Codex reviewer, waits for quota resets, never switches
provider silently), `steps/P1..P21-spec.md` + `steps/P22F1/P22F2-spec.md`, `gate-prompt-1.md`,
`gate-prompt-2.md`, `gate-report-1.md`, `gate-report-2.md`, `plan-residuals.md`, `obs-trial-record.md`,
`obs-trial-probe.py`, `campaign.log`.
