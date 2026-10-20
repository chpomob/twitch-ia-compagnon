# Step P4 — The guarded transport

Plan step `P4` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- Transport seam `Transport.open(method, *, scheme, host, port, path_and_query, pinned_addresses,
    headers) -> Response(status, headers, async read(max_bytes) -> bytes, close())`. `read(n)`
    returns **at most `n`** body bytes (fewer at a chunk boundary, `b""` at end of stream) and never
    hands the module a byte beyond `n`; there is no unsized chunk iterator on the seam, so the module
    cannot pull a whole chunk past its allowance. The **pinned addresses** are
    the list `resolve_checked` returned: a transport may connect only to them, so a name that
    resolves differently later cannot reach a refused address (AC10). The module never hands a
    transport a bare host name to resolve.
  - Default `AiohttpTransport`: one `aiohttp.ClientSession(trust_env=False)` (no proxy from the
    environment) built lazily on the first call (so `prepare` opens nothing, AC4); each request goes
    through a `TCPConnector(resolver=_PinnedResolver(host, pinned_addresses), use_dns_cache=False)`
    so aiohttp's own resolution returns only the checked addresses while TLS SNI and `Host` keep the
    name; `allow_redirects=False`, `auto_decompress=True`, `ClientTimeout(total=request_timeout +
    margin)` as a backstop; `Response.read(n)` is `response.content.read(n)` (aiohttp's
    `StreamReader.read(n)` returns at most `n` bytes), never `iter_chunked`/`iter_any`. Closed in
    `close()`.
  - `fetch(url, *, apply_allowlist, accept_body)`: per hop — `parse_target` → allowlist (reads only;
    refused `host_not_allowed` before resolution, so 0 connections) → `resolve_checked` → `open`. On
    301/302/303/307/308 with a `Location`, close the response without reading the body, resolve the
    location against the current URL, increment the hop count; more than `max_redirects` redirects →
    `too_many_redirects` (a chain of 4 with limit 3 makes exactly 4 connections); each hop re-checked
    in full (scheme, userinfo, allowlist, addresses). Non-redirect responses are returned to the
    caller with the final URL and the redirect count.
  - Body reading: `read_bounded(response, max_response_bytes)` keeps `allowance =
    max_response_bytes + 1 - consumed` and loops `response.read(min(64 KiB, allowance))` while
    `allowance > 0` and the stream is not at its end; each request asks for at most the remaining
    allowance, so the bytes consumed from the stream never exceed `max_response_bytes + 1` whatever
    the server's chunking. When the allowance reaches 0 it issues no further read and closes the
    response; it retains at most `max_response_bytes` and reports `truncated` iff the extra byte
    arrived (R4/AC13). A `read` returning more than requested is a transport contract violation
    → `error transport_failed` (defensive check, tested with a misbehaving fake).
  - Whole-call deadline: the entire call (resolution, every hop, body) runs as one task raced
    against `self._sleeper(request_timeout_seconds)` (default `asyncio.sleep`; tests pass
    `ManualClock.sleep` from `tests/conftest.py:105`); on expiry the work task is cancelled, the
    response closed, and the call ends `timeout timed_out` — strictly before the executor's 15 s
    deadline since the range caps the setting at 14.
  - Failures map to codes, never to exception text: connection/reset/TLS/DNS failure →
    `transport_failed`; `asyncio.CancelledError` from the caller re-raised.

## Requirements

Plan mapping: R4, R6
Acceptance criteria owned by this step: AC10, AC11, AC13, AC25
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

[P2, P3] (needs P2's guard; ordered after P3 because both edit
  `modules/web/__init__.py` and `tests/test_web.py`)

All dependencies are already merged into `main` when this step runs.

## Tests

With `FakeResolver`/`FakeTransport`: AC10 rebinding — a resolver answering a public
  address at the first lookup and `127.0.0.1` afterwards → the transport records 0 connections to
  `127.0.0.1` (the module resolves once per hop and pins). AC11 — a public page redirecting to
  `http://127.0.0.1/` → `blocked_address` after exactly 1 connection; 4 redirects with
  `max_redirects: 3` → `too_many_redirects` after exactly 4 connections; a chain of 3 → success,
  `redirects == 3`; a redirect to `ftp://` → `unsupported_scheme`. AC12 redirect half — `example.org`
  → `example.com` under `[example.org]` → `host_not_allowed` after 1 connection. AC13 — with
  `HTTP_PROXY`/`HTTPS_PROXY` set (monkeypatched), 0 recorded requests to the proxy host/address; a
  3 MiB body in 64 KiB chunks with `max_response_bytes: 1048576` → the fake stream, whose `read(n)`
  serves `min(n, rest of current chunk)` and records every requested `n` and every delivered byte
  count, delivers ≤ 1048577 bytes in total (exactly 1048577 here), ≤ 1048576 retained, `truncated`
  true; every recorded request `n` is ≤ the allowance remaining at that call (the last request is
  for exactly 1 byte, since 1048576 is a multiple of 64 KiB), and 0 reads are issued after the
  allowance reaches 0. The same with chunk sizes 1000 and 100 000 (not dividing the bound) → still
  ≤ 1048577 delivered. A body of exactly 1048576 bytes → 1048576 retained, `truncated` false (the
  extra read returns `b""`). A fake whose `read(n)` returns `n + 1` bytes → `error transport_failed`. a fake server that never sends a body → `timeout timed_out` at `request_timeout_seconds`
  on the `ManualClock`. Default transport, without sockets: `AiohttpTransport` built with a recording
  session-factory seam asserts `trust_env=False` and `allow_redirects=False`; `_PinnedResolver.resolve`
  returns exactly the pinned addresses for the pinned host and refuses any other host. AC25 half — a
  test that activates the module with **no** seams under `BRAVE_API_KEY` set, runs `prepare`, and
  asserts the socket guard recorded 0 attempts (no connection is made before a call).

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

Non-trivial (network boundary, concurrency/cancellation ordering). aiohttp's
  `TCPConnector` skips the resolver for IP literals — harmless because literals were already checked
  and pinned to themselves, but the `_PinnedResolver` must still be correct for names. `Location`
  may be relative, protocol-relative or contain userinfo — resolved with `urljoin` then re-parsed by
  `parse_target`. Cancelling the work task must close the response (in `finally`) so no socket
  leaks. aiohttp's `StreamReader` may buffer more bytes from the socket internally than `read(n)`
returns; R4/AC13 bound the bytes the module consumes from the stream, which is what `read(n)`
delivers — the socket-level buffer is bounded by aiohttp's own read-buffer limit, stated in the guide.
`auto_decompress` means a small gzip body may inflate: the byte bound counts decompressed
  bytes, which is the bound that protects the extractor. Real-socket pinning is proven only by unit
  checks of the resolver and by the operator trial; the summary states it.

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
