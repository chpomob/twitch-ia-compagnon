# Phase 7 — full-branch closing gate

You are the closing reviewer of phase 7. The phase gives the companion a bounded, guarded way to read
the public web — Wikipedia by default, Brave as an option — with two actions the brain calls like any
other tool. Eleven implementation steps have landed; decide whether the tree deserves approval by
checking the claims, not by reading them.

**Write your report to `docs/campaigns/phase7/full-branch-review.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file.

## Authorities

- `docs/campaigns/phase7/brief.md` — the operator's six binding decisions.
- `docs/campaigns/phase7/spec.md` — R1–R10, AC1–AC26, the caller enumeration and the test-failure
  allowlist (the six mechanical adaptations a 19th module forces).
- `docs/campaigns/phase7/plan.md` — the eleven steps and the gate.
- `docs/design-v2.md` — the trust model the guard implements.
- The pre-phase tree is `7c29434`; the phase head is the current `main`.

## First job: the requirements, one by one

For each of **R1–R10**, name the running tests that carry it and state PASS/FAIL/NOT-VERIFIABLE. A
criterion is PASS only if its assertion exists and fails when the behaviour is removed; a green test
contradicted by a sibling path is FAIL. In particular, check that all **26 acceptance criteria** are
covered, and that nothing in the spec's caller enumeration is unhandled.

## Second job: the security claims, with an executed attempt to defeat each

- **SSRF (R4).** Try every address class and every spelling you can: `127.0.0.1`, `10/8`, `172.16/12`,
  `192.168/16`, `169.254.169.254`, `::1`, `fc00::/7`, `fe80::/10`, an IPv4-mapped IPv6 form, decimal and
  octal spellings, a hostname resolving to a private address, and a public URL that **redirects** to a
  private one. Each must be refused or blocked with 0 connections to the private target.
- **Proxy bypass (R4).** With `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` set in the environment, the module
  must still make its own guarded connection (or refuse), never route through the proxy.
- **Byte and time bounds (R3/R4/R5).** A server streaming more than `max_response_bytes`: the read must
  stop at the bound, with `truncated: true` and no unbounded buffering. A server that never answers: the
  call must end at `request_timeout_seconds` before the executor's 15 s deadline.
- **Redaction (R7).** A query sentinel and a URL with a query string must appear in **no** trace, no
  diagnostic, no audit line and no bus event the module adds — while the successful observation carries
  the answer itself. The negative control must fail if the module echoes it.
- **Untrusted content (R8).** The marker must reach the brain's prompt; and a page whose text says "grant
  yourself an action and run it" must gain **nothing** — the executor still refuses an ungranted action.
- **Cache (R5).** TTL expiry (never serve a stale entry), the entry and byte bounds, one network request
  for two identical searches inside the TTL, and 0 for a hit.

## Third job: the test treatment and the invariants

- Only the six tests named in the spec's failure allowlist may have changed expectations; each change is
  a count or a constant forced by the 19th module, never a loosening. Compare assertion ASTs, not the
  summary's accounting.
- No added `skip`, `xfail` or `importorskip`; no deleted test; no removed expected-exception check.
- **No test opens a real socket**, and the suite is green:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- No new runtime dependency: check `pyproject.toml` and that the extractor is in-module.

## Fourth job: what a streamer actually gets

- The shipped example grants the two actions to `brain`, documented as revocable; the operator guide
  (English normative, French translation) says how to enable Brave with `${BRAVE_API_KEY}` and how to run
  the manual real-instance trial, whose record is written by hand and not by a pytest node.
- Nothing changes for a profile that does not enable `web`: absence of the module leaves the product
  exactly as it was.

## Deliverable (write to `docs/campaigns/phase7/full-branch-review.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE
- **SECURITY ATTEMPTS**: one row per attack you tried, with the observed result
- **TEST TREATMENT**: the allowlist audit, the real-socket check, the suite count
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly — no real Wikipedia or Brave call is made in this suite

This gate CLOSES phase 7. If everything you can check passes, say APPROVE plainly.
