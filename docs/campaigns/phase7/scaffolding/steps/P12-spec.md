# Step P12 — Full-branch review gate (R10; AC26 gate half; all of R1–R10)

Plan step `P12` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

A full-branch review by the reviewer model over `7c29434..HEAD` with **all changed
  files together**, as phases 5 and 6 did. Per-commit reviews miss cross-file bugs by construction:
  each commit can be correct in isolation yet broken in combination — here, P4's pinning with P6's
  provider endpoints (a provider request that bypassed `resolve_checked` would pass every per-step
  test), P7's cache with P9's redaction (a cache key that reaches a log line), P5's classifier with
  P6's backends (the same HTTP status mapped differently by action), P1's manifest marker with P8's
  framing (a renamed result key that no longer sorts first) — so only a review of the integrated diff
  sees them. The report records, executed and failing, at least one counterexample attempt each for
  R4 (e.g. a DNS-rebinding resolver, a decimal-literal loopback, a redirect to `[::ffff:10.0.0.1]`),
  R7 (an injected query through each failure path scanned per AC19) and R8 (the injection page with
  a forged marker and delimiter), maps every requirement to its named test, and must end `APPROVE`;
  a `REQUEST_CHANGES` loops back to the owning step and the next pass is tracked as
  `full-branch-review-2.md`, and so on.

## Requirements

Plan mapping: R10, R1, R10
Acceptance criteria owned by this step: AC26
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

[`docs/campaigns/phase7/full-branch-review.md`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P11]

All dependencies are already merged into `main` when this step runs.

## Tests

The counterexample attempts run and recorded with their outputs; the full suite green at
  the reviewed HEAD; `tests/test_web.py` run under `BRAVE_API_KEY` set with the socket guard reporting
  0 attempts (AC25).

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

Gate fix rounds touch earlier steps' files; P11's summary must be updated with every new
  closing commit before the final pass.

## Caller enumeration

No existing public function, protocol or structural type changes signature (spec, "Caller
enumeration"; re-checked with `grep -rn "ActionProvider\|_observation_content\|\.register(" core
modules`). The module plugs into existing seams only:

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/loader.py | manifest discovery | Discovers `modules/web/module.yaml` like every other manifest; no change. |
| core/actions.py | `ActionRegistry.register` via `context.actions.register` | Called by `WebModule.prepare` for the two specs; no change. |
| core/actions.py:2102 | `ActionExecutor._completed_payload` | Reads `provenance["provider"]` and `trace_fields` the module supplies; no change. |
| modules/brain/__init__.py:2518 | `_observation_content` | Renders the module's result unchanged; R8 relies on its existing `sort_keys=True` JSON (verified, P8). |
| modules/brain/__init__.py:3955 | `_offered_tools` | Offers the two reads per their `web` scope once granted; no change. |
| core/main.py:1038 | `check_config` | Runs `validate_settings` through the manifest hook; no change. |
| tests/test_examples.py, tests/test_manifest_presentation.py, tests/test_profiles.py, tests/test_config_ui.py, tests/test_docs_bilingual.py | shipped-manifest/guide enumerations | Allowlisted constant/count updates (P1, P10). |
| (unknown) | suite-wide module enumerations (`tests/test_hygiene.py`, `tests/test_status_record.py`, `tests/test_redaction_surfaces.py`, `tests/test_integration.py`, `tests/test_agentic_loop.py`) | Not on the allowlist; expected to pass unchanged; a failure stops P1 and is flagged. |

## Findings addressed

| Finding | Step(s) | Change |
|---|---|---|
| P1 (blocker) bounded read | P4, P5 | Transport seam exposes `read(max_bytes)` returning at most `max_bytes`; `read_bounded` requests at most the remaining `max_response_bytes + 1` allowance; tests record each requested size and delivered bytes with chunk sizes that do and do not divide the bound. |
| P2 (major) overlapping edits unordered | P3, P4, ordering rationale | P3 depends on [P1, P2], P4 on [P2, P3]: every step editing `modules/web/__init__.py`/`tests/test_web.py` is now in one dependency chain. |
| P3 (blocker) cache accounting | P7 | Cache stores the complete `ActionObservation`; size is key bytes + compact sorted-key JSON of all five observation fields; tests recompute from raw entries via `dataclasses.fields` and include omission and sensitivity checks. |

## Requirement coverage

R1 → P1, P5, P6 · R2 → P3, P6 · R3 → P3, P5 · R4 → P2, P4, P5, P6 · R5 → P1, P5, P7 · R6 → P4,
P5, P6 · R7 → P5, P6, P7, P9 · R8 → P1, P5, P6, P8 · R9 → P1, P10 · R10 → P1 (socket guard), P4,
P10, P11, P12.

## Ordering rationale

P1 comes first because the manifest is the contract every later step implements against, and
creating a 19th shipped manifest immediately breaks the allowlisted count tests and the package-data
guard — so the manifest, the `pyproject.toml` line and those adaptations land together and the suite
stays green; P1 also creates the test file with the socket guard every later test runs under. P2
(guard) and P3 (extractor) are logically independent, but every code step from P1 to P9 edits the
same two files (`modules/web/__init__.py`, `tests/test_web.py`), so the dependency graph is a single
chain: each step depends on the one before it (P3 on P2, P4 on P3 as well as P2), and no two steps
touching those files are ever unordered. P2 precedes P4 because the transport may connect only to
the addresses the guard returns; P3 comes between them only to serialise the shared-file edits. P4
builds the guarded fetch engine on P2; P5 is the first provider (`web.read`) because it exercises
the guard, transport and extractor directly and owns the shared classifier and admission; P6 adds the
search backends on top of P5's classifier and fetch path. P7's cache wraps both providers, so it
follows both. P8 proves R8 through the brain once both actions return their final result shape; P9
runs the cross-surface R7 scan last among code steps because it must cover every surface the earlier
steps created (cache, classifier, both backends, debug lengths). P10 documents the finished
behaviour and executes the guide's fragments against it. P11 records evidence once every closing
commit exists, and P12 is the full-branch gate over the integrated diff, last by construction.

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
