# Step P7 — The shared bounded TTL cache (R5; AC15, AC16)

Plan step `P7` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`_ObservationCache(clock, ttl, max_entries, max_bytes)` in process memory only,
  shared by both actions. Key: compact sorted-key JSON of `{"action": name, "provider": name,
  "arguments": canonical arguments}` (read: provider `http`). Stored value: the **complete
  `ActionObservation`** the provider returned (`core/contracts.py:1599`), not a reduced copy. Its
  canonical form for accounting is `_observation_record(obs) = {"status": obs.status, "result":
  obs.result, "error": obs.error, "parts": list(obs.parts), "provenance": obs.provenance}` — all five
  dataclass fields, so every title, URL, excerpt, text, `advisory`, `final_url`, `truncated`,
  `provider` and every `provenance.trace_fields` entry (`query_bytes`/`url_bytes`, `result_count`,
  `text_bytes`, `dropped_count`, `redirects`, `cache_hit`, `outcome`) counts. Entry size =
  `len(key.encode("utf-8"))` + `len(json.dumps(_observation_record(obs), ensure_ascii=False,
  sort_keys=True, separators=(",", ":")).encode("utf-8"))`, computed once at `put` and stored next
  to the entry. `get` drops and misses any entry whose age on the runtime clock
  exceeds the TTL; `put` stores only successes, refuses an entry larger than `max_bytes`, and evicts
  least-recently-used entries until both the entry and byte bounds hold; `cache_ttl_seconds: 0`
  disables the cache entirely. Lookup happens after admission (a hit still holds no network and
  releases its slot at once); a hit returns a new observation equal to the stored one except
  `provenance.trace_fields.cache_hit: True` (the stored entry, and so its accounted size, is left
  unchanged) and 0 resolutions/connections. The cache exposes `stats() -> (entries, stored_bytes)` (counts
  only) and a test-only `_snapshot() -> list[tuple[str, ActionObservation]]` (the raw entries) for
  tests; keys and entries never leave the object (no `__repr__` of contents, never logged).

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC15, AC16
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

[P6]

All dependencies are already merged into `main` when this step runs.

## Tests

AC15 — two identical searches within the TTL → 1 request, second trace `cache_hit:
  true`; after `cache_ttl_seconds + 1` on the `ManualClock` → a 2nd request; `cache_ttl_seconds: 0`
  → 2 requests for 2 searches; a `refused` (429) and an `error` (500) followed by a retry each send a
  new request. AC16 — `cache_max_entries: 3`, 5 distinct successful calls → 3 entries;
  `cache_max_bytes: 65536` with 20 distinct 8 KiB reads → after each read the stored content,
  recomputed independently by the test from `_snapshot()` with the R5 formula over the **whole
  observation** — the test's own serialiser walks `dataclasses.fields(ActionObservation)` (so a field
  the cache's `_observation_record` forgot would still be counted by the test) — is > 0, ≤ 65536,
  and equal to `stats().stored_bytes`. Per-entry check: for one cached read and one cached search,
  the accounted entry size equals the test's whole-observation size and is strictly greater than
  `len(key) + len(compact JSON of result alone)` by at least the compact length of the
  `provenance` mapping (so an implementation accounting only the result plus counts fails).
  Sensitivity check: two reads whose results are identical but whose `final_url` differs by 100
  bytes, and two searches differing only in one result `title`, differ in accounted size by exactly
  that byte difference. a read of `https://example.org/x` and a search for the same string are 2 distinct entries.

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

Non-trivial (ordering/eviction invariants). Computing size from a reduced view of
  the observation (result only, or result plus counts) would under-count and let the real footprint
  exceed `cache_max_bytes`: the cache accounts the complete observation, and the test recomputes from
  the raw stored observations via `dataclasses.fields`, not from the cache's own record helper, so
  the two cannot share an omission. `provenance`/`result` are mappings that may hold tuples or
  `MappingProxyType`; `_observation_record` converts them to plain JSON types deterministically and
  the test's serialiser does the same independently (a non-JSON value is a bug, surfaced as a test
  failure, never `default=str`). A cached `truncated` read is
  served as is — acceptable since the settings are fixed for the process. Clock going backwards
  (ManualClock never does) is treated as fresh only while age ≥ 0.

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
