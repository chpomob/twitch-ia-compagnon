# Step P21 — Operator guide (EN/FR), design addendum (EN/FR), indexes, bilingual registration, combination tests (R8, R9, R10, D3, D5, D6; AC37, AC39, AC40, AC42)

Plan step `P21` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **The guide, `docs/automation-bridge.md`** (marked normative). It covers:
    - what the bridge does and does not do;
    - every setting;
    - the allowlist, including a statement that a group entry delegates the choice of reachable actions to whoever edits that group in the instance;
    - ownership and no fallback;
    - the R4 outcome table, worded so that it never claims execution;
    - events lost while disconnected;
    - the cue recipe;
    - the trust model (A5, and P11's risk);
    - the trial status;
    - the **combination table, 12 rows**, each with non-empty "enabled", "disabled" and "refused" cells. Row 8 states that families and cues do not reach the brain in the agent-process placement.
  - **Profile fragments.** Each row has one fenced YAML fragment, marked with `<!-- combination N base=<profile> -->`, so the test can extract it.
  - **`docs/automation-bridge.fr.md`.** A French translation with the same sections and fenced blocks, every backticked identifier, and the statement that the English text wins.
  - **The design addendum, `docs/design-v2.md`** (and its translation). It covers the input-ownership rule: the vocabulary, D1, D2 resolution, the runtime guard (D3/D4, including the guarded v1 route), gated binding (D5), engine identities (D6) and the ownership-aware hook (D7).
  - **`docs/README.md`.** List both guide files, and add the phase-5 trial row with status "not delivered — awaiting real-instance trial".
  - **`README.md`.** Add the bridge to the module list with the same status.
  - **`tests/test_docs_bilingual.py`.** `GUIDES` gets `"automation-bridge"`.
  - **`tests/test_phase5_docs_status.py`.** Remove the P20 `xfail` marker. `tests/test_phase5_trials.py` is not touched.

## Requirements

Plan mapping: R8, R9, R10
Acceptance criteria owned by this step: AC37, AC39, AC40, AC42
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-5 decisions that override any contrary reading of a requirement:
- **The bridge is optional in every case.** Absent, disabled or unreachable, the companion behaves
  exactly as it does today: no setting becomes required at startup, no default path changes, and no
  shipped profile depends on it. An unreachable instance produces a *ready* companion with an
  explicitly unavailable capability — never a failed startup.
- **The minimum scope is the sink and the triggers, together**: the sink runs a named action of the
  user's instance behind our own authorization, deadlines and honest terminal outcomes; the triggers
  let an event the instance routes enter the companion through the ordinary trigger policies.
- **Its events may REPLACE our basic inputs (chat, viewers) — never in parallel.** For every
  (family, platform) pair there is at most one active provider, resolved at preparation from the
  profile and the manifests, and an ambiguous configuration is refused with a diagnostic naming the
  family and both providers. Counting on the deduplication window to hide two providers is excluded.
- **Do not hide the integration's defects.** An execution the instance cannot confirm is never
  reported as a success: an acknowledged run ends `external_unknown` with its cause, and the model
  and the operator both see it.
- **This phase is NOT "delivered" at merge.** The README and the documentation index carry
  "not delivered — awaiting real-instance trial" until the trial record exists, and a test enforces it.
- **The vendor and product names never appear in `core/`.** The kernel vocabulary stays vendor-free:
  action `automation.action.run`, permission `automation.run`, destination scope `automation`,
  families `chat`/`viewers`/`cues`.
- **The approved plan's deviations D1–D8 are decisions of this phase** and override any contrary
  reading of the specification — including the two files beyond the specification's cost row:
  `core/actions.py` (`excluded_platforms`, D5) and `core/triggers.py` (`add_companion_identities`,
  D6). The D8 transmission-boundary canary must stay green; if an aiohttp upgrade breaks it, take the
  fallback route D8 documents and say which route you took in the commit message.
- The configuration UI stays a **local-only** surface; the ownership table and its diagnostics are
  displayed **read-only**, and nothing in the UI can widen the authorization set or write the
  `secrets` and `actions` blocks.

## Files

[`docs/automation-bridge.md`, `docs/automation-bridge.fr.md`, `docs/design-v2.md`, `docs/design-v2.fr.md`, `docs/README.md`, `README.md`, `tests/test_docs_bilingual.py`, `tests/test_automation_bridge.py`, `tests/test_phase5_docs_status.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P19, P20]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_automation_bridge.py`:
  - **AC39.** Extract the 12 fragments, apply each to a tmp copy of the stated shipped profile, set the test `${NAME}` values, and run `--check-config`. Rows 1–8 exit 0. Rows 9–12 exit non-zero with the documented diagnostic substring, all under a socket-creation spy (0 transports).
  - **AC40.** The table has 12 rows, no cell is empty, and row 8 contains the sentence.
  - **AC42.** `tests/test_docs_bilingual.py` passes with the pair. The English file marks itself normative, and the French file states that the English text wins.
  - **AC37** (`tests/test_phase5_docs_status.py`) now passes unmarked, and `tests/test_phase5_trials.py` still collects exactly 8.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 3,124 passed / 18 skipped, the count at the start of
this phase; the seven allowlisted tests are adapted as the specification lists them, never weakened.

## Risks

- **Row 7** (remote brain, `wss://`) must check without a reachable host. `--check-config` opens nothing, so it passes.
  - **Row 8** uses `agent.yaml.example` and relies on the proxy's actions allowlist finding the run action in the discovered catalog. The test must assert this through check-config, not a live proxy.
  - The bilingual check requires every backticked token of the English text to appear in the French one. Write the French text last, from the final English text.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase5): ...`, `fix(phase5): ...`, `test(phase5): ...`, `refactor(phase5): ...`, `docs(phase5): ...`).
  The scope MUST be `phase5`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase5)`.
- Keep the phase-0 to phase-4 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, the phase-3 moderation/memory/watch semantics, and the phase-4 configuration UI's
  local-only, read-only-for-secrets-and-actions surface.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
