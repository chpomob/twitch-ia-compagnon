# Step P5 — `web.read`

Plan step `P5` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- `prepare` now registers `web.read` with a `_ReadProvider` (spec built from the colocated manifest
    as `_declared_screen_capture_spec` does, `modules/capture/__init__.py:952`), provider name `web`.
  - The read path: admission (below) → `fetch(url, apply_allowlist=True)` → status mapping → content
    type check on the headers only (`text/html`, `application/xhtml+xml`, `text/plain`; anything
    else → `refused unsupported_content_type` with the response closed before any `read` is
    issued) → `read_bounded` → `extract_text` → `cut_utf8(text, max_text_bytes)` →
    `ActionObservation(status="success", result={advisory: MARKER, final_url, title?, text,
    truncated: body_cut or text_cut}, parts=())`.
  - One classifier `_classify(...) -> (status, code)` used by both actions (the R6 table): guard
    refusals and `resource_busy` → `refused`; 401/403 → `refused provider_refused`; 429 →
    `refused rate_limited`; 404/410 on a read → `refused not_found`; other non-2xx →
    `error upstream_status`; transport failure → `error transport_failed`; undecodable/malformed →
    `error malformed_response`; deadline → `timeout timed_out`. Error messages are fixed per code
    (e.g. `"web.read refused: blocked_address"`).
  - Concurrency: a non-blocking in-flight counter; a call arriving with `max_concurrent_requests`
    already in flight is refused `resource_busy` before any resolution or connection; the slot is
    released in `finally`.
  - Every observation carries `provenance={"provider": "http", "trace_fields": {"url_bytes":
    len(url.encode()), "text_bytes": n, "redirects": k, "cache_hit": False, "outcome": code or
    "ok"}}` — counts and lengths only.

## Requirements

Plan mapping: R3, R4, R5, R6, R7, R8
Acceptance criteria owned by this step: AC1, AC2, AC8, AC9, AC13, AC17, AC18
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

[`modules/web/__init__.py`, `tests/test_web.py`, `tests/fixtures/web/page_20k.html`, `tests/fixtures/web/PROVENANCE.md`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3, P4]

All dependencies are already merged into `main` when this step runs.

## Tests

Through a real `ActionExecutor` with a grant for `brain` on `twitch/<chan>/web` (the
  pattern of `tests/test_capture.py`): AC1 provider half — no rule → `refused not_authorized`, the
  provider invoked 0 times, 0 connections. AC2 url rows — missing `url`, a 2049-character `url`, an
  extra key → `invalid_arguments`, 0 connections. AC8 — a 20 000-byte-text page (`page_20k.html`)
  with `max_text_bytes: 8192` → ≤ 8192 bytes, valid UTF-8, `truncated: true`; a 1000-byte page →
  `truncated: false`; `application/pdf` → `refused unsupported_content_type` with 0 `read` calls
  recorded. AC9/AC10/AC11/AC12 re-run end-to-end as executor statuses and codes. AC13 (a) a `text/html`
  body of exactly 1048576 bytes whose text is 1000 bytes → that text, `truncated: false`; (b) a
  20 000-byte `text/plain` body → exactly 8192 bytes, `truncated: true`; the stalled body →
  executor status `timeout`, code `timed_out`, before the 15 s deadline. AC17 — `max_concurrent_requests:
  2`, 2 held fake requests, a 3rd call → `refused resource_busy` with 0 additional connections, and
  succeeds after one held request completes. AC18 read rows — 404, 410 → `refused not_found`; 500,
  503 → `error upstream_status`; connection reset → `error transport_failed`; stalled body →
  `timeout timed_out`. Every success result validates against the manifest result schema and has
  0 `text` parts.

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

Non-trivial (branching classification, executor boundary). The executor may rewrite a
  provider-returned `timeout` through `_uncertain_status` (`core/actions.py:1793`) — for a `read` it
  should stay `timeout`, verified by the AC13/AC18 rows, and the provider's own `timed_out` code must
  survive; if the executor substitutes its own code, stop and flag (no executor edit). A `refused`
  status from a provider must be accepted by the executor as a terminal status (kick and youtube
  already return it). A HEAD-before-GET is not used: the content-type check reads the GET headers
  only, so a refused type still costs 1 connection (AC8 counts body bytes, not connections).

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
