# Step P2 — The network guard

Plan step `P2` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Pure, synchronous guard functions plus one async resolution step, no I/O of their
  own:
  - `parse_target(url) -> Target | GuardRefusal`: `urllib.parse.urlsplit`; scheme lower-cased must be
    `http`/`https` else `unsupported_scheme`; any userinfo (`@` in the netloc), an empty host or an
    unparsable port → `unsupported_url`. Host normalised: strip IPv6 brackets, lower-case, trailing
    dot removed, IDNA-encode non-ASCII labels (`str.encode("idna")`; a failure → `unsupported_url`).
  - `literal_address(host) -> IPv4Address | IPv6Address | None`: `ipaddress.ip_address` for
    canonical forms, then `socket.inet_aton` for every legacy IPv4 spelling the platform resolver
    accepts (decimal `2130706433`, hex `0x7f.1`, octal `017700000001`, short forms), so a literal is
    classified as an address and never sent to the resolver as a name.
  - `address_refusal(addr) -> "blocked_address" | None`: unwrap IPv4-mapped (`::ffff:a.b.c.d`) and
    IPv4-compatible (`::a.b.c.d`, high 96 bits zero, excluding `::` and `::1`, which are refused as
    unspecified/loopback) to the embedded IPv4 and check that; refuse loopback, private, link-local,
    shared `100.64.0.0/10`, unspecified, multicast, `255.255.255.255`, documentation
    (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, `2001:db8::/32`), benchmarking
    (`198.18.0.0/15`), `240.0.0.0/4`, `0.0.0.0/8`, `192.0.0.0/24`, `fc00::/7`, `fe80::/10`, site-local
    and any other address whose `is_global` is false. The table is explicit (not `is_global` alone)
    because `ipaddress` classification differs across the supported Python versions (`>=3.10`).
  - `host_allowed(host, allowed_hosts)`: exact equality after the same normalisation (case-insensitive,
    IDNA); no suffix or wildcard match; empty list allows any host; applied only to reads, never to
    the fixed provider endpoints.
  - `async resolve_checked(target, resolver) -> list[address] | GuardRefusal`: a literal is checked
    directly; a name is resolved once through the `_resolver` seam (default:
    `loop.getaddrinfo(host, port, type=SOCK_STREAM)`); the call is refused `blocked_address` if **any**
    returned address is refused (AC9 last row), `transport_failed` if resolution fails or is empty.
    The returned list is what the transport (P4) is allowed to connect to — the pinned addresses.

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC9, AC10, AC12
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

[P1]

All dependencies are already merged into `main` when this step runs.

## Tests

Parametrized over every AC9 URL (`127.0.0.1`, `[::1]`, `10.0.0.1`, `172.16.0.1`,
  `192.168.1.1`, `169.254.169.254`, `[fe80::1]`, `[fc00::1]`, `100.64.0.1`, `0.0.0.0`, `224.0.0.1`,
  `255.255.255.255`, `192.0.2.1`, `198.18.0.1`, `240.0.0.1`, `[::ffff:127.0.0.1]`, `[::127.0.0.1]`,
  `2130706433`, `0x7f.1`, `017700000001`) → `blocked_address` with 0 resolver calls for literals;
  the mixed public + `10.0.0.5` name → `blocked_address`. AC10 refusal half: `ftp://`, `file:///etc/passwd`,
  `gopher://` → `unsupported_scheme`; `http://user:pw@example.org/` → `unsupported_url`; plus a host-less
  URL → `unsupported_url`. AC12 matching: `EXAMPLE.org` allowed by `[example.org]`; `sub.example.org`
  and `example.com` refused; an IDNA host (`bücher.example` vs `xn--bcher-kva.example`) equal both ways.
  A public literal (`93.184.216.34`, `[2606:4700::1111]`) passes. The guard's refusal objects carry
  only the code (asserted: no attribute holds the URL or host).

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

Non-trivial (security boundary, platform-dependent parsing). `inet_aton` accepts forms
  that `ip_address` rejects and vice versa; both paths are tested. A host like `1.2.3` is a legal
  `inet_aton` literal — treated as an address, which is the safe side. NAT64 (`64:ff9b::/96`) and
  6to4 embed IPv4 too; the spec lists only mapped/compatible, so these are left to `is_global`
  (which treats `64:ff9b::/96` as global) and the gap is recorded in the module docstring and the
  P10 summary as "not covered" rather than silently assumed.

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
