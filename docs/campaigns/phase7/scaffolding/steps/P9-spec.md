# Step P9 — Nothing this module emits or records carries the query, a URL path/query string, the key or page text (R7; AC19, AC20)

Plan step `P9` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add the `debug_lengths` diagnostics: the module logs through
  `logging.getLogger("modules.web")` one value-free line per non-success call (`web.search refused:
  rate_limited provider=brave`) and, only with `debug_lengths: true`, appends `query_bytes=`,
  `url_bytes=`, `response_bytes=`, `text_bytes=` and nothing else. Audit every string the module can
  produce (error messages, `module.degraded` reasons, log lines, exceptions raised from
  `activate`/`prepare`, `__repr__` of settings/backends/cache) so each is a fixed template over fixed
  vocabulary and integers; the settings object's `repr` omits `brave_api_key`. Fix any leak the scan
  below finds.

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC19, AC20
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

[`modules/web/__init__.py`, `tests/test_web.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P8]

All dependencies are already merged into `main` when this step runs.

## Tests

AC19 — an end-to-end run with the fake input module delivering the viewer chat message
  `SECRETQ zebra 42 ?x=1` to the brain (fake model proposes `web.search` with it) and a read of
  `https://example.org/p/SECRETPATH?token=SECRETQS#frag`, each run five times: success, `refused`
  (429 / `host_not_allowed`), `error` (500), `timeout` (stalled), and success with `debug_lengths:
  true`, under `provider: brave` with a fake key and again under `wikipedia`. Collected by emitter:
  every `action.*` trace payload and audit stdout line of the two calls (the shipped `audit` module
  wired as in `tests/test_audit.py`), every `module.*` trace and `module.degraded` reason from `web`,
  every bus event published by `web` (expected none), `caplog` + `capsys` stderr for `modules.web`,
  raised exception text, observation `error.message`, the status-record entries for `web`, and any
  cache key/entry appearing in those — 0 occurrences of `SECRETQ`, `zebra`, `SECRETPATH`, `SECRETQS`,
  `token=`, the fake Brave key, or any 8-byte substring of the fake page text or excerpts. The input
  module's own chat event is excluded by emitter, nothing of `web` is. The search's
  `action.completed` carries `query_bytes: 21`. A search for `wikipedia` under the Wikipedia provider
  succeeds with `provider: wikipedia`, `query_bytes: 9`, and no recorded field other than fixed
  vocabulary equals or contains the query. A negative control injects the query into one log line
  and asserts the scan fails. AC20 — with `debug_lengths: false` the diagnostics contain 0 length
  fields; with `true` they contain the query/URL byte length and nothing from the forbidden list.

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

Non-trivial (cross-surface invariant the type system cannot check). The executor's or
  brain's own records are not this module's, but AC19 scans every `action.*` payload of the calls —
  P5/P6 verified the executor's payload holds no arguments (`core/actions.py:2102`); if a surface
  outside `web` leaks the query, stop and flag rather than editing core. aiohttp may log on its own
  logger (`aiohttp.client`) with URLs — the scan includes all log records emitted during the calls,
  and the default transport is never used in tests, so a real-transport log leak is called out in the
  guide's "what is recorded" section and checked in the gate. The 8-byte-substring scan over page
  text can false-positive on fixed vocabulary; fixture text is chosen with no 8-byte overlap with
  field names, codes or the marker (asserted in the test setup).

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
