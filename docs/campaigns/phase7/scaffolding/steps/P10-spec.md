# Step P10 — Operator guide (EN normative, FR translation), README, and the configuration-only on/off proof (R9, R10 trial procedure; AC23, AC24, AC25 docs half)

Plan step `P10` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- `docs/web-knowledge.md` (marked normative): the two actions and their contracts; the 16 settings
    (`provider`, `brave_api_key`, the 13 R5 settings, `debug_lengths`) in one table with default and
    range equal to the manifest; the provider choice; the network guard and its limits (incl. the
    NAT64 note from P2 and real-socket pinning proven by unit checks only); what is recorded (the
    trace fields of R7) and what never is; the untrusted marker text and the JSON framing of P8;
    the **grant fragment** (fenced YAML: the `web:` module entry and two `actions:` rules for
    principal `brain` on `platform: twitch`, `channel_id: ${TWITCH_BROADCASTER_ID}`, `scope: web`,
    marked revocable by deleting the two rules); the **Brave fragment** and the exact steps (obtain a
    key, `export BRAVE_API_KEY=…`, set `provider: brave` and `brave_api_key: ${BRAVE_API_KEY}`, run
    `--check-config`); the manual operator-trial procedure (Wikipedia; Brave when a key exists) and
    its record file `docs/campaigns/phase7/web-trial-record.md`. Each fragment is preceded by a
    stable HTML comment anchor (`<!-- web-knowledge:grant-fragment -->`,
    `<!-- web-knowledge:brave-fragment -->`) so the test extracts exactly that block.
  - `docs/web-knowledge.fr.md`: the translation, linking the English guide and stating the English
    text wins; same sections and identical fenced blocks (the bilingual test's rules).
  - `README.md`: capability list entry, project-layout row for `modules/web` and the guide, and the
    proven/not-proven section: providers proven against fakes only.
  - `tests/test_docs_bilingual.py`: add `web-knowledge` to `GUIDES` (allowlisted; nothing else).

## Requirements

Plan mapping: R9, R10
Acceptance criteria owned by this step: AC23, AC24, AC25
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-7 decisions that override any contrary reading of a requirement:
- **Wikipedia works with no key; Brave is an option.** A profile with no credential must get a
  working Wikipedia search; Brave is configured, never required, and its key is a `${...}` reference.
- **Two actions only**, `web.search` and `web.read`. No combined action and no separate
  `wikipedia.summary`: Wikipedia is a provider behind the search contract.
- **The network guard is the only way out.** Every request, at every hop, goes through R4: no
  loopback, private, link-local or non-public address, redirects re-checked, the byte bound measured
  while streaming, the whole call bounded, and no proxy environment variable may ever be honoured.
- **Every bound is a setting with a validated range** (R5), and nothing returns more than its bound.
- **The query is never written.** A query may be a viewer's words: traces and diagnostics carry the
  provider, lengths, counts and the outcome, never the text. The in-memory cache key is not a record;
  it never leaves the process.
- **Read content is data.** The observation carries the untrusted marker, the brain renders it as
  data, and no configuration of this module can grant a capability: default-deny is untouched.
- **No new runtime dependency.** HTML becomes text with the in-module extractor; if a library looks
  unavoidable, stop and report instead of adding one.
- **No test opens a real socket.** Every test uses a fake transport; the real-instance trial is a
  documented manual procedure with its own record, never a pytest node.
- Close exactly the requirement(s) this step names. If a sibling path shows the same defect, report
  it in your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. The full suite is green at every step:
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (never the system python: no aiohttp).

## Files

[`docs/web-knowledge.md`, `docs/web-knowledge.fr.md`, `README.md`, `tests/test_docs_bilingual.py`, `tests/test_web.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P9]

All dependencies are already merged into `main` when this step runs.

## Tests

AC23 — the test reads the grant fragment from the English guide by its anchor, applies
  it to a temp copy of `config.yaml.example`, and `check_config` exits 0 with exactly 2 rules naming
  `web.search` and `web.read` for principal `brain`; with those 2 rules deleted, the brain's offered
  tools for the served channel contain 0 `web.*` actions; the Brave fragment applied the same way
  exits 0 with `BRAVE_API_KEY` exported and non-zero without it. AC24 — both files exist, the English
  one says it is normative, the French one says the English text wins; the settings table in each
  guide is parsed and every one of the 16 settings appears with default and range equal to the
  manifest's (ranges held by the validator compared to the validator's constants); the README links
  `docs/web-knowledge.md`; `pyproject.toml` declares exactly `aiohttp` and `PyYAML` and the
  `modules.web` line; `git diff 7c29434 -- '*.example'` has 0 changed lines in the 4 shipped
  profiles. AC25 docs half — both guides describe the operator trial and name
  `docs/campaigns/phase7/web-trial-record.md`. AC25 structural half — an AST scan of
  `tests/test_web.py` asserts every `activate(` call passes the `_transport` seam except the single
  P4 no-call test, and that no other test file imports `modules.web`. The full bilingual suite passes
  with the new pair.

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

Trivial-to-moderate (docs), except that the fragments are executable: if the guide's
  YAML drifts from the module the AC23 test catches it. The bilingual test requires every
  `snake_case` identifier of the English guide in the French one — setting names stay untranslated.
  Memory note: editing a shipped `*.example` profile ripples into fixture tests; this step must not
  touch any profile (AC24 asserts it).

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase7): ...`, `fix(phase7): ...`, `test(phase7): ...`, `refactor(phase7): ...`, `docs(phase7): ...`).
  The scope MUST be `phase7`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase7)`.
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
