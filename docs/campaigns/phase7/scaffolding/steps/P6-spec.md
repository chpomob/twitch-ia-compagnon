# Step P6 — `web.search`

Plan step `P6` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- `prepare` registers `web.search` with a `_SearchProvider` delegating to the configured backend;
    a backend is an object with `name` and `build_request(query, limit) -> (url, headers)` and
    `parse(body) -> (results, dropped) | Malformed`, so a third provider adds a class and an enum
    value, not a contract field (R1).
  - `WikipediaBackend`: `GET https://{wikipedia_language}.wikipedia.org/w/api.php?action=query&list=search&srsearch=<q>&srlimit=<max_results>&srprop=snippet&format=json&formatversion=2&utf8=1`,
    `User-Agent: twitch-ia-compagnon/<version> (web module)` plus `; <user_agent_contact>` when set;
    no credential read. Parse `query.search[]` (`title`, `snippet`); missing/ill-typed structure →
    `malformed_response`; a well-formed empty list → success with 0 results. Result `url` =
    `https://{lang}.wikipedia.org/wiki/` + `quote(title.replace(" ", "_"), safe="/:()_,'-.")`.
  - `BraveBackend`: `GET https://api.search.brave.com/res/v1/web/search?q=<q>&count=<max_results>`,
    header `X-Subscription-Token: <resolved key>`, `Accept: application/json`; parse
    `web.results[]` (`title`, `url`, `description`); a missing `web.results` list or non-JSON →
    `malformed_response`.
  - Shared: a single result lacking a title or an `http(s)` URL is dropped and counted; excerpts are
    passed through `extract_text` (markup such as `<span class="searchmatch">`/`<strong>` removed,
    entities decoded) then `cut_utf8(max_excerpt_bytes)`; results kept in provider order, truncated
    to `max_results`. The provider endpoint goes through `fetch(..., apply_allowlist=False)` — the
    same scheme/address guard and pinning, never the read allowlist; a 2xx body is read under
    `max_response_bytes` and must be `application/json` or parseable JSON.
  - Result `{advisory: MARKER, provider: backend.name, results: [...]}`; provenance
    `{"provider": backend.name, "trace_fields": {"query_bytes": len(query.encode()),
    "result_count": n, "dropped_count": d, "redirects": k, "cache_hit": False, "outcome": ...}}`.
  - Fixtures follow the MediaWiki `list=search` and Brave Web Search API documentation; each is named
    with its source URL in `PROVENANCE.md`.

## Requirements

Plan mapping: R1, R2, R4, R6
Acceptance criteria owned by this step: AC2, AC3, AC5, AC6, AC12, AC18
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

[`modules/web/__init__.py`, `tests/test_web.py`, `tests/fixtures/web/mediawiki_search_8.json`, `tests/fixtures/web/mediawiki_search_empty.json`, `tests/fixtures/web/brave_web_search_3.json`, `tests/fixtures/web/brave_web_search_no_results.json`, `tests/fixtures/web/PROVENANCE.md`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P5]

All dependencies are already merged into `main` when this step runs.

## Tests

AC2 query rows — `query: ""`, 257 characters, missing `query`, an extra key →
  `invalid_arguments`, 0 connections. AC3 request half — default profile settings: exactly 1 request,
  to `{lang}.wikipedia.org` path `/w/api.php`; `wikipedia_language: fr` → `fr.wikipedia.org`;
  `provider: brave` with the key: exactly 1 request to `api.search.brave.com` `/res/v1/web/search`
  carrying `X-Subscription-Token` equal to the key, and 0 requests to any Wikipedia host. AC5 —
  `mediawiki_search_8.json` (snippets with `<span class="searchmatch">` and `&amp;`, one snippet whose
  50th byte falls inside a multi-byte character), `max_results: 5`, `max_excerpt_bytes: 50` → exactly
  5 results in order, each excerpt with 0 `<`, 0 `&amp;`, ≤ 50 bytes, valid UTF-8, each URL
  `https://en.wikipedia.org/wiki/<title with _>` percent-encoded. AC6 — `brave_web_search_3.json`
  with one result missing `url` → success, 2 results, trace `dropped_count: 1`;
  `brave_web_search_no_results.json` (no results list) and a non-JSON body → `error
  malformed_response`, 0 results. AC12 search half — `allowed_hosts: [example.org]` and a search →
  still reaches the provider endpoint. AC18 search rows — Brave 401/403 → `refused
  provider_refused`; 429 → `refused rate_limited`; 500/503 → `error upstream_status`; malformed JSON
  → `error malformed_response`; empty MediaWiki list → `success`, 0 results. One aggregate test
  walks the 12 AC18 rows (guard refusals as one) and asserts only the last is `success`.

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

Non-trivial (two external API shapes, credential use). The Brave key is a resolved
  credential value: it must only ever reach the request header — never a message, trace, cache key
  (the cache key uses the provider *name*) or `repr` of the backend (`__repr__` overridden to omit
  it). MediaWiki `snippet` HTML is attacker-influenced text; it goes through the same extractor so
  no markup survives. Percent-encoding rules for titles must match what Wikipedia serves (a test
  pins `Café (film)` → `Caf%C3%A9_(film)`). Brave omits `web` entirely for some empty answers in
  practice; the spec classifies a missing list as malformed — implemented as specified and recorded
  in the guide as known behaviour.

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
