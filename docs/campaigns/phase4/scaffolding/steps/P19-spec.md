# Step P19 — Full-suite regression and full-branch review gate (AC11; all R1–R10)

Plan step `P19` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Suite:** run `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` with no `*.local.yaml` present in the repository. Require at least 2,460 passed and at most 18 skipped, plus the tests added in this phase, with no failure outside the two allowlisted tests.
  - **Full-branch review:** review `git diff main...HEAD` as one change set, across all files together. Per-commit reviews miss cross-file bugs by construction: each step can be correct in isolation while the combination is wrong. Examples:
    - P4's digest and P14's drift both depend on P2's `canonical_digest` being fed the *same* merged unresolved document;
    - P12's Check and P13's Save must share `_apply_edits`, or Check validates a different draft from the one Save writes;
    - P9's socket-creation patch must still hold after P12/P15 add event loops and subprocesses (the fixture's creation counter is 0 across the whole module);
    - `serve()`'s handler must reach `handle` only through `_dispatch` (D8): grep `core/config_ui/__init__.py` for any `ui.handle(`/`self.handle(` call inside an `async def`, and for any `asyncio.run(`;
    - every `Popen` in the supervisor must be paired with a started `_StderrDrain` (D9);
    - the redaction guard (P10) must cover the P15 restart reports;
    - the P5–P8 manifest edits must not break P11's path-set equality.
  - **Gate checklist:**
    - every AC1–AC43 has a named test;
    - `core/config_ui` names no module (AC17);
    - no write path other than the overlay, the status record, and the UI's own temp files;
    - no external URL;
    - `[project].dependencies` unchanged;
    - the caller table in P3 still matches a fresh grep of the call sites;
    - `spec.md` targets equal the files touched (`git diff --name-only main...HEAD`, excluding `spec.md`/`plan.md`).
  - Record any deviation (e.g. the D1–D9 decisions, D7's reliance on private CPython loop methods, the P4 fallback if D5 did not hold) in the review summary. `spec.md`/`plan.md` are updated only to record such a deviation, never to relax a requirement.

## Requirements

Plan mapping: R1, R10
Acceptance criteria owned by this step: AC11
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-4 decisions that override any contrary reading of a requirement:
- The configuration UI runs in a **separate process** (`python -m core.config_ui`); it is a
  **local-only** surface (loopback by default, an access token, a Host/Origin and CSRF guard),
  never a remote administration channel, and it adds **no runtime dependency** beyond aiohttp
  and PyYAML, with no CDN or external asset.
- Its writing scope (decision 6c) is exactly `enabled_modules`, `modules.<name>`, `triggers`,
  `limits` and `modules_directory`. The `secrets` block and the `actions` block (the default-deny
  authorization rules) are **read-only in v1 and displayed read-only**, so a single mis-click can
  never widen the set of authorized actions.
- Writes go to a **managed overlay file** merged over the untouched base file (comments preserved);
  a secret VALUE never appears in any page, response, log or diagnostic, only its reference and its
  set/unset state.
- Every module page is **generated from the module manifest** (schema plus presentation metadata);
  **no module ships HTML**, and adding a module must add its page with no UI code change.
- Applying a change is a **supervised restart** (there is no hot reload in this phase).

## Files

[`docs/campaigns/phase4/spec.md`, `docs/campaigns/phase4/plan.md`]

**Do not overwrite the repository-root `spec.md` / `plan.md`**: those are the frozen v1-MVP
documents. The phase-4 specification and plan are `docs/campaigns/phase4/spec.md` and
`docs/campaigns/phase4/plan.md` — never write the phase-4 documents to the repository root.

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14, P15, P16, P17, P18]

All dependencies are already merged into `main` when this step runs.

## Tests

The full suite (AC11); the gate checklist above, with each item's evidence (test name or grep output) attached.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- Suite runtime grows with the real subprocess tests in P15. Keep the windows short.
  - A flaky subprocess test must be fixed deterministically (injected clock), never retried or skipped.

## Requirement coverage

| Requirement | Steps |
|---|---|
| R1 | P9 (guards, bind, token, socket-creation patch for AC5), P17 (console script), P16 (token not logged) |
| R2 | P2 (merge, path, read), P3 (runtime and CLI), P13 (base never written) |
| R3 | P1 (validator), P5–P8 (17 manifests), P8 (allowlisted users test) |
| R4 | P10 (base and core pages), P11 (module pages and triggers), P14 (readiness and running state) |
| R5 | P9 (socket-free loop, request bridge), P12 (Check) |
| R6 | P9 (startup refusals), P13 (save and remove) |
| R7 | P2 (digest, collision helper), P4 (publisher, runtime collision), P9 (UI collision), P14 (reader and drift), P15 (Apply) |
| R8 | P10 (secret set, redaction), P16 (canary sweep) |
| R9 | P10/P11 (inline assets, escaping), P16 (URL audit), P17 (dependencies) |
| R10 | P18 |

## Ordering rationale

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase4): ...`, `fix(phase4): ...`, `test(phase4): ...`, `refactor(phase4): ...`, `docs(phase4): ...`).
  The scope MUST be `phase4`: the repository history is full of `feat(phase3):` / `fix(phase2):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase4)`.
- Keep the phase-0 to phase-3 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, and the phase-3 moderation/memory/watch semantics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
