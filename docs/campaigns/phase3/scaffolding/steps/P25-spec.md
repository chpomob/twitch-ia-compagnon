# Step P25 — README phase 3 section and hygiene checks (R8, R1-table; AC7, AC41)

Plan step `P25` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add a `## Phase 3` section in the phase 1/2 style, with
these subsections:
  - **Versioning.** Each of the 6 allowlisted tests, named with its fate.
  - **Presence table.** At least 11 rows (welcome, thanks, summary,
    translation, polls, scenes, voice, jingles, screen reactions, clips,
    memory), plus rows for channel-point redemptions, clip-link
    announcement and anonymous-gifter thanks. The "configuration ou code"
    cell names the module. Chat-command polls and channel-point redemptions
    say "code".
  - **Platform capability matrix.** Named API limits:
    - Kick public-HTTPS webhook reachability, timeout-only moderation and
      minute rounding;
    - YouTube daily quota, polling, 200-character sends;
    - Twitch scopes (P7, P8);
    - no clip or poll API on Kick or YouTube.
  - **Memory model.** Format, bounds, eviction order, erasure paths, and the
    honest limits: plain unencrypted files, per-platform-and-channel
    identities never merged, a crash may lose the last write, and the
    lone-note rule from P10.
  - **Moderation.** The modes with the default `alert` and the strict rules
    (and the P14 index limit).
  - **Watch trigger.**
  - **Trial table.** Exactly six rows in R8 order, each with commit, settings
    shape (`${…}` only), outcome (`PHASE3-TRIAL …` or `Non exécuté — <reason>`),
    limits and date. It names `tests/test_phase3_trials.py`.
  - **`tests/test_hygiene.py`** gains, reusing `_table_cells` and
    `_allowlisted_tests`:
    - the six rows, each with 5 non-empty fields, a 7–40-hex commit, no
      `SECRET_SETTING` match, `PHASE3-TRIAL` or `Non exécuté` in the
      outcome, and a date matching `YYYY-MM-DD`;
    - the section names the trials file;
    - the versioning subsection names every test of
      `docs/campaigns/phase3/spec.md`'s allowlist;
    - the presence table has at least 11 rows with non-empty cells, and
      polls and redemptions say "code".

## Requirements

Plan mapping: R8, R1
Acceptance criteria owned by this step: AC7, AC41
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.
Phase-1 product decision that overrides any contrary reading: delivery is a **configured, pluggable
terminal step** — a configured ordered list of delivery actions (mode `fixed`, or mode `modules`
derived from the enabled modules that declare a delivery capability, with a configurable preference
order), each entry declaring how the answer text maps into its arguments (a named argument, or none
for an effect-only delivery such as a stream-scene change); every entry runs at the terminal step
only, each receiving the text only if declared; the model never selects the delivery and never
performs an intermediate effect; adding a delivery module must require no change to the agentic loop.

## Files

[docs/README.md, tests/test_hygiene.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P23, P24]

All dependencies are already merged into `main` when this step runs.

## Tests

the new hygiene tests (AC7, AC41) plus a parser self-test on
  samples, like `test_the_phase_2_readme_checks_read_the_documents_shape`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- The hygiene test must read the phase 3 archived spec
    (`docs/campaigns/phase3/spec.md`), not the root `spec.md`, which the
    implementation never modifies. Confirm that the archived copy carries
    the same allowlist, and re-archive under the permitted scope if the spec
    moves.
  - The commit hash in the trial rows is the commit the trials ran on.
    "Non exécuté" rows still need a hash (the base commit).

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase3): ...`, `fix(phase3): ...`, `test(phase3): ...`, `refactor(phase3): ...`).
- Keep the phase-0 guarantees in force: bounded admission, phase lifecycle, explicit terminal action
  outcomes, default-deny authorization (reads included), bounded retention, a single global
  startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss diagnostics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- No new runtime dependency beyond the existing ones unless this step is the one that adds it; no
  model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
