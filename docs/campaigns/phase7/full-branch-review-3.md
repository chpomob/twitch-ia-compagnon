# Phase 7 — full-branch closing review, third pass

## VERDICT: REQUEST_CHANGES

Reviewed `7c29434..07f6bd6d859a613a7e337eafd0ac25dbc5bf015d` on 2026-10-20 against the brief, R1–R10 / AC1–AC26 / Amendment A1, plan, design-v2, and both earlier reports. P12F4 resolves F1a and N1 and fixes F1b's late-success classification and retained entry. **F1b remains PARTIAL: the exact delayed-store probe still exceeds the configured whole-call deadline.** N2's substantive corrections are present, but its current-head test counts and diff totals remain stale.

Only `docs/campaigns/phase7/full-branch-review-3.md` was written; nothing was staged or committed. The working tree was initially clean. Probe wrappers were process-local; Python bytecode and pytest's cache provider were disabled. The command sandbox failed to initialize (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`), so commands used the approved elevated path. Standalone rigs explicitly refused real socket connection and resolver entry points.

## ITEMS SETTLED

All timed executor probes below used `request_timeout_seconds=1`, `rig.handle._sleeper=None` (the real asyncio timer), fake public transport/resolution, and `time.monotonic()` around `await rig.read(...)` or `await rig.search(...)`. Response limit was 8,388,608 bytes for F1a/F1b/N1 and the serialization control. Cache TTL was zero for F1a/N1; default enabled TTL for delayed insertion/serialization. No parser or timer was patched.

| Item / executed reproduction | Elapsed (s) | Status | Configured time bound holds? | Settlement |
|---|---:|---|---|---|
| F1a: exact unfinished 8 MiB tag | 0.768755 | `success`, empty text | Yes: finishes before 1 s | RESOLVED |
| F1a: exact repeat | 0.754098 | `success`, empty text | Yes | RESOLVED |
| F1b: original 1.2 s wrapper around cache `put` | **1.201051** | `timeout timed_out`; cache `(0 entries, 0 bytes)` | **No: 0.201051 s overdue** | **PARTIAL** |
| F1b: repeat on read, with independent timer heartbeat | **1.201615** | `timeout timed_out`; cache `(0, 0)` | **No** | PARTIAL |
| F1b: same wrapper on search | **1.201710** | `timeout timed_out`; cache `(0, 0)` | **No** | PARTIAL |
| Serialization-delay control: `record_size` delayed 1.2 s | 1.001841 | `timeout timed_out`; cache `(0, 0)`, still empty after worker finishes | Yes, allowing normal timer-delivery overhead | Passing control |
| N1: exact 6,000,058-byte Wikipedia answer | 1.001087 | `timeout timed_out`; result/dropped counts both 0, cache `(0, 0)` | Yes, allowing normal timer-delivery overhead | RESOLVED |

### F1a — RESOLVED

Exact body, unchanged from pass two:

```python
body = b'<p' + b' a' * ((8388608 - 2) // 2)
```

Both runs used `read_rig(max_response_bytes=8388608, request_timeout_seconds=1, cache_ttl_seconds=0)`, scripted `html_page(body)` on `example.org`, and called `read('https://example.org/')`. The new stepped tag-end scan finishes this body before the deadline. An unfinished tag contributes no text; its timely empty success is legitimate. The additional unfinished-quoted-attribute, end-tag, cooperative-stop and standard-parser equivalence tests also passed in the full web run.

### F1b — HIGH — PARTIAL

At `modules/web/__init__.py:2655`, `_cached` still calls `_cache.put(...)` synchronously on the event loop. The new clock check at lines 2656–2659 correctly discards the entry and returns a timeout after an overdue store. It cannot deliver the terminal result while the store is blocking.

The exact original latency wrapper was re-executed:

```python
original_put = web._ObservationCache.put

def delayed_put(self, *args, **kwargs):
    time.sleep(1.2)
    return original_put(self, *args, **kwargs)

with patch.object(web._ObservationCache, 'put', delayed_put):
    # read_rig(max_response_bytes=8388608, request_timeout_seconds=1)
    # rig.handle._sleeper = None; scripted html_page(b'<p>ok</p>')
    start = time.monotonic()
    observation = await rig.read('https://example.org/')
    elapsed = time.monotonic() - start
```

The repeated read and search probes also scheduled `loop.call_later(1, ...)` immediately before invocation. Those independent one-second callbacks ran at **1.201245 s** and **1.201417 s**, respectively. The loop itself is blocked until insertion returns. This distinguishes the deterministic 20% overrun from the roughly 1–2 ms timer-delivery overhead in the stalled-fetch and serialization controls.

The serialization control instead wrapped `record_size` with the same `time.sleep(1.2)` and its original call, using `staticmethod` when patching the class. It returned at 1.001841 s, and the cache remained empty after another 0.3 s. Thus serialization is deadline-raced; insertion still is not.

The former late success and retained entry are fixed on both actions. No late success was observed in this pass. This injected latency probe does **not** establish that ordinary cache insertion normally takes 1.2 s; it establishes the remaining unbounded scheduling path requested by this gate. R4 requires the whole call to end within the configured setting. Keeping the timer alive and checking expiry after blocking work establishes honest classification, not that elapsed-time guarantee. The summary itself acknowledges this distinction at lines 89–90; it is not an amendment to R4.

Remaining correction: make cache insertion/completion respect the call's remaining time while preventing abandoned work from storing a late entry. Add an elapsed-time assertion to the real-clock delayed-store regression; its current test checks status/cache/trace but never measures duration.

### N1 — RESOLVED

Exact original payload and settings were retained:

```python
body = json.dumps({'query': {'search': [
    {'title': 'Valid', 'snippet': '<p>' * 2000000},
]}}).encode()  # exactly 6,000,058 bytes
rig = await t.search_rig(max_response_bytes=8388608,
                         request_timeout_seconds=1, cache_ttl_seconds=0)
rig.handle._sleeper = None
rig.transport.script('en.wikipedia.org', t.FakeResponse.body(
    body, headers={'Content-Type': 'application/json'}))
# Measure await rig.search('q') with time.monotonic().
```

The result is now timely `timeout timed_out`, with no partial result, count or stored answer. JSON decoding, snippet extraction, result construction and cache-record sizing run under the shared deadline. The ordinary search control remains successful. The original read reproduction, `b'<p>' * (8388608 // 3)`, also returned `timeout timed_out` at **1.000863 s**, with no cache entry.

### N2 — LOW — PARTIAL

The four specifically requested substantive corrections all pass inspection:

- Three skip paths are described as **removed by P12F3**, not present. The executed AST scan confirms zero web skip/xfail/importorskip calls or markers.
- AC30 is explicitly the seventh allowlisted node under A1; the exact bare-`web` comment is described as caught, consistent with the passing A2 predicate tests.
- The evidence head names P12F4, which is current head `07f6bd6`; naming this self-containing commit without its own hash is acceptable.
- The fix ledger cites P3F1 `38e14c5`, P12F2 `d18da61`, P12F3 `d31bf17` and P12F4, with named tests. Executed collection/commit checks found **131 test IDs, 0 missing; 16 commit hashes, 0 missing**.

However, the summary is not fully current. It still states **467 web tests** at line 188; the executed current-head web run passes **474**. Its line 227 says `+6,917 / −15` test lines and `test_web.py +6,246`; executed `git diff --numstat 7c29434 -- tests` gives **15 files, +7,005 / −15**, including **`test_web.py +6,334`**. These are current-P12F4 claims, not figures labeled as belonging to an older evidence head. Its line 177 also states **4,390 passed / 26 skipped** at P12F4; the required current-head full-suite run gives **4,397 passed / 26 skipped** below. Refresh those evidence numbers. Historical discussion of the original findings is otherwise appropriate and is not a new finding.

## Executed controls and whole-branch checks

Required full-suite command, unmodified:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
4397 passed, 26 skipped in 312.13s (0:05:12)
```

Independent run with `BRAVE_API_KEY=PHASE7_GATE_FAKE_KEY`, bytecode/cache disabled, and a `sys.addaudithook` below the socket monkeypatches:

```text
tests/test_web.py: 474 passed in 25.64s
socket.__new__: AF_UNIX=554; AF_INET=0; AF_INET6=0
socket.connect=0; socket.bind=0
socket.getaddrinfo=1: ('127.0.0.1', 1, 0, 0, 0)
```

The Unix constructors are asyncio's existing event-loop socket pairs. No phase-owned socket constructor was added; the guard self-test still uses its stand-in. There was no provider connection. Existing Kick loopback tests and harness are unchanged from `7c29434`; the second pass's distinction between those sockets and a phase-added socket remains applicable.

| Permissive/control case | Measured result |
|---|---|
| Short `<p>ok</p>` read | `success`, exact text `ok`, **0.000631 s** |
| Normal Wikipedia fixture search | `success`, 5 results, 0 dropped, **0.001351 s** |
| Exactly 1 MiB ordinary `<p>x</p>` tags | `success`, 8,192 text bytes, **0.543144 s** |
| P12F4's richer ordinary-tag page (1,048,555 bytes; div/p/b, attributes, entity and title) | `success`, title `Long`, 65,536 text bytes, `truncated=true`, **0.250892 s** |
| Fetch whose body never answers | `timeout timed_out`, **1.001737 s** |
| 3 MiB streamed plain text, 1 MiB response bound | `success`, **0.015282 s**; exactly **1,048,577** bytes delivered, 8,192 text bytes, `truncated=true`; every read respects remaining allowance |
| Twenty 8 KiB reads, 65,536-byte cache cap | 20 successes/requests; independent whole-observation storage is **8,706–60,956 bytes**, always positive and within cap; final cache **7 entries / 60,956 bytes** |

The 26 skips are the same pre-existing environmental/opt-in total as in pass two; no web test skipped.

All AC13 byte-bound/chunking tests and AC15–AC16 tests passed in the 474-test run: hit/expiry/TTL-zero, refusal/error retry, exact whole-observation accounting, least-recent eviction, overlarge-entry handling, and distinct read/search keys. F2's malformed-sibling/all-malformed regressions also pass. F2–F5 remain resolved.

The detailed named-test map in the first two reports is unchanged. All 26 criteria retain mapped coverage; this does not imply the manual approval criterion has passed:

| Requirement | Criteria | Third-pass assessment |
|---|---|---|
| R1 | AC1–AC4 | Contracts, default-deny, provider/key selection, argument validation and connection-free preparation pass. |
| R2 | AC5–AC6 | Bounded/plain results and malformed-sibling preservation pass. |
| R3 | AC7–AC8 | Extraction, hidden elements, charsets, UTF-8 and text/type limits pass, with permissive markup controls above. |
| R4 | AC9–AC13 | Address/rebinding/redirect/host/proxy and streamed-byte controls pass; whole-call timing remains partial under F1b. |
| R5 | AC14–AC17 | Settings, admission and cache bounds pass; F1b still limits the elapsed-time claim. |
| R6 | AC18 | Status table and overdue read/search probes report honest `timed_out`; no executed late success remains. |
| R7 | AC19–AC20 | Both providers × outcome/debug scans and leak-detection controls pass. |
| R8 | AC21–AC22 | Actual brain-framed forged-marker/delimiter content remains data; tools unchanged, scene action refused. |
| R9 | AC23–AC24 | Grants/revocation, executable bilingual guide fragments, settings/dependencies and unconditional profile-diff checks pass. |
| R10 | AC25–AC26 | Fake-transport/manual-trial checks and citation validation pass; F1b and the remaining N2 evidence refresh prevent the approving AC26 gate. |

Executed security counterexample attempts still fail to bypass their protections: `test_ac10_a_rebinding_name_never_reaches_loopback_end_to_end` and redirect-refusal cases cannot reach the private target (R4); AC19's sentinel query/URL/key/page scans are clean and its injected-log control detects the intentional leak (R7); both `test_ac21_ac22_an_injection_page_is_framed_data_and_grants_nothing` cases retain the forged instructions as framed data, preserve tools and refuse the ungranted scene proposal with zero provider invocations (R8). These all ran in the web run; no real upstream request was used.

The executed AST comparison against `7c29434` finds no deleted pre-existing test function, new skip/xfail/importorskip call or marker, or further assertion loosening. Existing test changes remain confined to the same five files and seven allowlisted count/set adaptations (including the bilingual shared constant and A1's AC30). A2's previously accepted strengthening remains the sole additional predicate repair; no new adaptation was introduced by P12F4. Exact set/equality assertions remain intact.

No new dependency: `git diff 7c29434 -- pyproject.toml` is still only the `modules.web` package-data line; runtime dependencies remain exactly `aiohttp>=3.9,<4` and `PyYAML>=6,<7`. Shipped `*.example` profile diffs remain empty. Brain, executor and loader are unchanged; the UI's only phase edit remains its docstring. `git diff --check` passes.

## REGRESSIONS

No new runtime regression was demonstrated. F1b is the remaining portion of the previously reported deadline gap; the honest outcome and removal of the late entry are improvements. N2 is a remaining evidence-refresh gap. The executed permissive controls and tests above pass.

## NEW FINDINGS

None beyond the remaining F1b and N2 portions documented above. No speculative issue is promoted to a finding.

## NOT VERIFIED

- No real Wikipedia or Brave call was made. Credentials, live API behavior, current upstream responses, TLS/SNI and pinning over an actual remote socket remain unverified; fake/recording sessions do not replace the manual trial.
- No hand-written operator trial record was produced. Its absence remains permitted by R10; live-service success is not claimed.
- The installed-distribution smoke test remains an environmental skip (this virtualenv has no pip). Direct manifest/package-data assertions ran; installed behavior is not established by this run.
- The first pass did not demonstrate unbounded decompression in installed aiohttp 3.14.3. Other supported aiohttp/Python versions and network-specific translated-address behavior remain untested; the earlier NAT64/6to4 observations do not establish behavior across versions or gateways.
- The changed CI checkout configuration was inspected but not executed on a hosted CI runner. The current full-history checkout satisfies AC24 locally.

REQUEST_CHANGES
