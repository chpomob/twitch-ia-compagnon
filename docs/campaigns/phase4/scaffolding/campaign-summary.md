# Phase 4 — campaign summary (final)

**Status: COMPLETE — full-branch closing gate APPROVED on 2026-09-30 (ninth pass).**

## Delivery

**What phase 4 adds**: a **local web configuration UI** served by a separate process
(`python -m core.config_ui`), where **every module exposes its own page generated from its manifest**,
reachable from a base page. The operator can see what is enabled and ready, check a draft without touching
the running process, write changes to an overlay, and apply them with a supervised restart.

- `main` at `9739db6`; **43 commits, 90 files, +22,572 / −40 lines** for the phase.
- **3,051 tests pass, 18 skip** (phase 3 close: 2,460/18). New suites: `tests/test_config_ui.py`,
  `tests/test_overlay.py`, `tests/test_status_record.py`, `tests/test_manifest_presentation.py`.
- New package `core/config_ui/` (+ `core/overlay.py`), operator documentation `docs/config-ui.md`,
  `default`/`title` annotations across all 17 manifests, `.gitignore` patterns for the generated files.
- No new runtime dependency: still `aiohttp` + `PyYAML`, fully offline (no CDN, no external asset).

## The operator's decisions, as shipped

| Decision | Shipped |
|---|---|
| **1b** separate process | `python -m core.config_ui` (+ console script), reads/writes config files, supervises the process it launched |
| **2a** overlay | a managed `*.local.yaml` merged over the untouched (comment-preserving) base; overrides removable; runtime and `--check-config` honour it |
| **3a** secrets never edited | only the reference and the set/unset state are shown; no secret value at any length, in any position, can be read back |
| **4a** supervised restart | Apply stops/restarts the child it launched, reports acceptance, and shows drift honestly (including "unknown" and an unusable record) |
| **5a** pages from the schema | one page per module from `settings_schema` + `title`/`default`; modules ship no HTML |
| **6c** writing scope | writes `enabled_modules`, `modules.<name>`, `triggers`, `limits`, `modules_directory`; `secrets` and `actions` are displayed read-only, with the reason |

## Review history — nine gate passes, twelve fix rounds

Each gate reviewed the **whole branch cumulatively** and re-ran its predecessor's counterexamples.

| Gate | Verdict | Outcome |
|---|---|---|
| 1 | REQUEST_CHANGES | F1–F5: a tilde overlay path bypassed the collision guard and Save targeted the **base file**; short secrets and configured keys bypassed the secret guard; a boolean `default: true` vanished; the dispatch queue was unbounded; the post-kill wait was unbounded |
| 2 | REQUEST_CHANGES | F4/F5 resolved; F1/F2/F3 partial; **N1** — the F2 fix rewrote structural tokens (`t[hidden]ue`) |
| 3 | REQUEST_CHANGES | F1/N1/F3 resolved; **N2** — Check retargeted positional fields after a key reorder |
| 4 | REQUEST_CHANGES | F2/N2 resolved; **N3/N4** — Check validated placeholders while Save restored real values; masking rewrote JSON punctuation |
| 5 | REQUEST_CHANGES | N4 resolved; N3 partial; **N5** — two keys containing a secret collapsed and an entry was **lost** |
| 6 | REQUEST_CHANGES | **N6** — a non-secret edit silently changed another entry's hidden key |
| 7 | REQUEST_CHANGES | N5 resolved; N6/N3 partial; **N7** — a withheld scalar could not be set to the empty string. **The operator chose the architectural fix**: the form transports edits over stable identities, never a reconstruction of masked text |
| 8 | REQUEST_CHANGES | N7 resolved; N6/N3 partial (uniform refusal); **N8** — the declared `.gitignore` patterns were missing |
| 9 | **APPROVE** | **every item RESOLVED** — N3/N6/N8 included; the gate-8 failures now meet the same side-effect-free 409 refusal on Check, Save and Remove |

Twelve fix steps (`P19F1`–`P19F12`), one atomic commit each, every one reviewed by the adversarial loop
(Claude DEV + Codex REVIEW) before landing. Three findings were **introduced by earlier fixes** (N1, N3/N4,
N5) and were caught by the next pass — the case for multi-pass review.

## Honest limits (the gate's own "NOT VERIFIED" list, plus harness notes)

- **No real browser session or JavaScript execution** was exercised: no live HTTP listener load test, no
  full navigation, no unsaved-edit preservation test in a real browser. The UI's behaviour is proven at the
  request/response and render level only.
- The 18 environment-dependent skips are unchanged (capture process-group cases, phase-2 and phase-3
  provider trials, distribution install).
- Real platform trials (Twitch, Kick, YouTube, OBS) were **not** re-run in this phase; phase 2's
  obs-websocket trial remains the reference for what a real trial looks like.
- **Harness lessons recorded**: a killed pipeline run leaves the repository on its own branch and can wipe
  untracked files (verify `git show --stat HEAD` after committing an artifact); a runner "RED" after
  approval can come from the loop's stash-restore reverting the working tree (compare `git status` with
  HEAD before believing it — `P19F12` was a false alarm of exactly that kind); a gate verdict of
  `unparsed` means the reviewer was cut off mid-review (quota or "model at capacity") and the gate must be
  re-run, never concluded from.

## Durable scaffolding (inside the repository)

`run_campaign_phase4.py`, `gen_step_specs_phase4.py`, `rerun_gate.py`, `steps/P1..P19` + `P19F1..P19F12`,
`gate-prompt-1..9.md`, `gate-report-1..9.md` (tracked on purpose this phase), `verify-by-hand-{1..5}.json`,
plus the approved `docs/campaigns/phase4/{brief,spec,plan}.md`.
