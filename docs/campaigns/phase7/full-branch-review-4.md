# Phase 7 — full-branch closing review, fourth pass

## VERDICT: APPROVE

Reviewed `7c29434..0b692f3d5160a24cbd12d226bd68f7da71958f70` (P12F5) on 2026-10-20, focusing on F1b and N2 and checking the third pass's accepted coverage for regressions. **F1b is resolved: neither terminal answer waits for the delayed cache insertion, the independent timers run on time, and abandoned insertions never land in the executed probes. N2's numerical claims match the current tree.** No blocking regression remains. This report closes the phase-7 gate.

The requested 474 / +7,005 / 4,397 figures describe the third-pass tree, before P12F5 added two tests and 106 test lines. The measured current figures are **476 web tests; 15 test files, +7,111 / −15, including `test_web.py` +6,440; 4,399 passed / 26 skipped overall**. Requiring the older figures at this head would make the summary inaccurate.

The working tree was initially clean. Only this report was written; nothing was staged or committed. Probe patches existed only in their Python processes. All Python commands disabled bytecode generation; pytest's cache provider was disabled. The sandbox could not initialize (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`), so execution used the approved elevated command path.

## ITEMS SETTLED

### F1b — HIGH — RESOLVED

The exact latency wrapper was executed with a short `b'<p>ok</p>'` read, the normal Wikipedia fixture for search, `max_response_bytes=8388608`, `request_timeout_seconds=1`, default enabled cache settings, and `rig.handle._sleeper=None`. Read used `read_rig`; search used `search_rig`. Both invoked the real executor over fake public transport/resolution. Real socket connection and resolver entry points were explicitly refused in these standalone processes.

```python
original_put = web._ObservationCache.put

def delayed_put(self, *args, **kwargs):
    time.sleep(1.2)
    return original_put(self, *args, **kwargs)

# Inside patch.object(web._ObservationCache, 'put', delayed_put):
start = time.monotonic()
loop.call_later(1, lambda: beats.append(time.monotonic() - start))
observation = await rig.read('https://example.org/')  # or await rig.search('q')
elapsed = time.monotonic() - start
immediate = tuple(rig.handle._cache.stats())
await asyncio.sleep(0.5)
after = tuple(rig.handle._cache.stats())
```

| Executed probe | Await elapsed (s) | Independent 1 s callback (s) | Observation | Cache at return / after another 0.5 s |
|---|---:|---:|---|---|
| Delayed `put`, read | **1.001624** | **1.001145** | `timeout timed_out` | **(0, 0) / (0, 0)** |
| Delayed `put`, search | **1.001998** | **1.001593** | `timeout timed_out` | **(0, 0) / (0, 0)** |
| Serialization delayed 1.2 s, read | 1.002133 | 1.001703 | `timeout timed_out` | (0, 0) / (0, 0) |
| Serialization delayed 1.2 s, search | 1.002454 | 1.002042 | `timeout timed_out` | (0, 0) / (0, 0) |
| Undelayed read | 0.000847 | Cancelled after the check | `success`, text `ok` | (1, 509) / (1, 509) |
| Undelayed search | 0.001375 | Cancelled after the check | `success`, 5 results | (1, 1,377) / (1, 1,377) |

Cache tuples are entries and stored bytes. The delayed probes asserted `1 <= elapsed < 1.1`, a callback before 1.1 s, the timeout status/code, and an empty cache both immediately and after 0.5 s. Both actions' completed traces also said `timed_out`. The additional wait exceeds the wrapper's remaining 0.2 s. No late entry or late success was observed. An earlier repeat with enabled TTL 300 also passed both delayed-store probes at 1.001664 / 1.001597 s.

Inspection confirms that `_cached` now runs insertion through `_Deadline.run` on a worker, revokes an abandoned store, and discards its own observation if necessary. `_ObservationCache.put` checks revocation inside its lock before mutation; its clock stamp is obtained before taking that lock. The timeout therefore remains deliverable while the injected store or store clock stalls.

Elapsed time is asserted in the tests, rather than inferred from their passing status. Executed AST inspection found the explicit **`assert 1 <= elapsed < 1.1, elapsed`** in all three:

- `tests/test_web.py::test_p12f4_a_store_that_outlives_the_deadline_is_timed_out_with_no_entry`
- `tests/test_web.py::test_p12f5_a_held_store_does_not_hold_the_answer_and_never_lands` — exercises both read and search, a live heartbeat, the late-entry check, and successful undelayed storage.
- `tests/test_web.py::test_p12f5_a_store_stalled_in_the_cache_clock_never_holds_the_lock` — also checks that lookup, stats and discard remain responsive.

These tests passed in both the independent web run and the full suite. Serialization-delay controls still return at approximately one second, with only normal millisecond timer-delivery overhead.

### N2 — LOW — RESOLVED for the three numerical claims

| Evidence | Third-pass figure requested in the prompt (superseded) | Executed current P12F5 result | Current summary |
|---|---|---|---|
| `tests/test_web.py` | 474 passed | **476 passed, 0 skipped**, 30.11 s | Matches |
| `git diff --numstat 7c29434 -- tests` | 15 files, +7,005 / −15; web +6,334 | **15 files, +7,111 / −15; web +6,440** | Matches |
| Full suite | 4,397 passed, 26 skipped | **4,399 passed, 26 skipped**, 322.80 s | Counts match |

`git diff --numstat HEAD^ HEAD -- tests` gives exactly `106  0  tests/test_web.py`; AST comparison finds exactly two new test functions. This completely accounts for the count and line changes. The summary contains the current totals and none of the superseded competing numerical claims. Historical figures explicitly identified with earlier heads remain historical.

There is a **nonblocking attribution nit**: the summary's evidence-head text at lines 8–11 and suite-command caption at line 173 still name P12F4. Its updated suite numbers actually describe P12F5, as independently verified here, and its diff paragraph correctly names P12F5. Those labels should read P12F5; they do not leave a competing numerical current total or invalidate the requirement/test ledger. No summary edit was made under this report-only authorization.

## REGRESSIONS

**None demonstrated.** F1a, N1 and the earlier F2–F5 settlements remain intact.

Additional standalone reproductions retained the original payloads, a one-second real timer and TTL zero:

| Previous reproduction | Elapsed (s) | Result |
|---|---:|---|
| F1a: `b'<p' + b' a' * ((8388608 - 2) // 2)` | 0.733625 | Timely `success`, empty text; unfinished tag discarded |
| Original read: `b'<p>' * (8388608 // 3)` | 1.000740 | `timeout timed_out`, cache empty immediately and after 0.5 s |
| N1: exact 6,000,058-byte Wikipedia JSON with `'<p>' * 2000000` snippet | 1.000776 | `timeout timed_out`, cache empty immediately and after 0.5 s |

Every executed overdue read/search probe reports `timed_out`; no overdue success was observed. Timely successes in the permissive controls remain legitimate.

Required full-suite command, executed unchanged:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
4399 passed, 26 skipped in 322.80s (0:05:22)
```

The independent web run used `BRAVE_API_KEY=PHASE7_GATE_FAKE_KEY` and a `sys.addaudithook` below socket mocks:

```text
476 passed in 30.11s
socket.__new__: AF_UNIX=558; AF_INET=0; AF_INET6=0
socket.connect=0; socket.bind=0
socket.getaddrinfo=1: ('127.0.0.1', 1, 0, 0, 0)
```

The Unix constructors are asyncio event-loop socket pairs. The guard self-test continues to use `_UnopenedSocket`, a stand-in; no phase-owned socket construction was introduced. Existing Kick loopback tests and their shared harness are unchanged from the accepted third-pass state and phase base. This audit establishes the web run's operations, not a claim that the full suite constructs zero kernel sockets.

The R1–R10 map and AC1–AC26 retain their previous definitions and coverage. The spec is unchanged since the third review; its only change against `7c29434` is the already accepted A1 allowlist amendment.

| Requirement | Criteria | Current assessment |
|---|---|---|
| R1 | AC1–AC4 | Contracts, default-deny, provider/key selection, validation and connection-free preparation pass. |
| R2 | AC5–AC6 | Bounded plain results and malformed-sibling preservation pass. |
| R3 | AC7–AC8 | Extraction, charsets, hidden elements and text/type limits pass; F1a remains resolved. |
| R4 | AC9–AC13 | Address/rebinding/redirect/host/proxy/stream bounds pass; the remaining whole-call scheduling probe now passes. |
| R5 | AC14–AC17 | Settings, admission, cache hit/expiry/TTL-zero/accounting/eviction and read/search key separation pass. |
| R6 | AC18 | Outcome table and deadline probes report honest statuses and codes. |
| R7 | AC19–AC20 | Both providers and all outcome/debug scans pass, including leak-detection controls. |
| R8 | AC21–AC22 | Forged instructions remain framed data, offered tools remain equal, and the ungranted scene action is refused. |
| R9 | AC23–AC24 | Executable bilingual grants/Brave fragments, revocation, settings, dependency and shipped-profile checks pass. |
| R10 | AC25–AC26 | Fake-transport/manual-trial documentation and citation checks pass; this approving report supplies the closing gate. |

R4/R7/R8 counterexample attempts were executed in the web run: `test_ac10_a_rebinding_name_never_reaches_loopback_end_to_end` cannot connect to the private rebound target; `test_ac19_nothing_web_emits_or_records_carries_the_query_url_key_or_text` cannot demonstrate a leak under either provider (and `test_ac19_the_scan_fails_on_a_query_injected_into_a_log_line` detects its intentional injected leak); both `test_ac21_ac22_an_injection_page_is_framed_data_and_grants_nothing` cases fail to escape framing or obtain scene authority, with zero scene-provider invocations. Their tests all pass: the attempted bypasses fail.

Executed AST comparison across the test tree finds **zero deleted pre-existing test functions, zero added skip/xfail/importorskip nodes, and zero web skip/xfail nodes**. Existing expectation adaptations remain the same seven allowlisted nodes, including AC30 and the bilingual shared constant. The previously accepted A2 predicate strengthening is unchanged; there is no additional loosening. All removed test lines were inspected. P12F5 adds two tests and strengthens the existing elapsed/late-entry regression without deleting an assertion. The full suite retains the same 26 environmental/opt-in skips; no web test skips.

No new dependency: the phase's only `pyproject.toml` change is the `modules.web` package-data line; runtime dependencies remain `aiohttp>=3.9,<4` and `PyYAML>=6,<7`. Shipped example profiles, brain, executor and loader remain unchanged. The UI's only phase edit is its already reviewed docstring. Summary citation validation finds **131 test identifiers, 0 missing; 16 commit hashes, 0 missing**. `git diff --check` passes.

## NEW FINDINGS

No new runtime or blocking finding. The executed summary inspection confirms only the nonblocking P12F4/P12F5 attribution nit described under N2; its current numerical claims are correct.

## NOT VERIFIED

- No live Wikipedia or Brave request, real upstream TLS/SNI/pinning trial, or operator-written trial record was produced. R10 explicitly permits the manual record to remain absent.
- The installed-distribution smoke test remains an environmental skip because this virtualenv lacks pip; manifest/package-data assertions ran.
- Other supported Python/aiohttp versions, network-specific translated-address behavior, and hosted CI execution were not exercised. The third pass's limits concerning NAT64/6to4 and decompression remain unchanged.

APPROVE
