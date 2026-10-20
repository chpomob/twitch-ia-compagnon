# Phase 7 — full-branch closing review, second pass

## VERDICT: REQUEST_CHANGES

Reviewed `7c29434..d31bf173567cc14a5a870555cd475d5ec09fc45d` on 2026-10-20 against the brief's six binding decisions, the spec including Amendment A1, the plan, design-v2 and the first closing report. F2–F5 are resolved. F1 is partially resolved: the original reproduction now times out promptly, but an unfinished HTML tag still defeats the elapsed-time bound, and cache insertion remains outside the deadline. An additional search reproduction returns a late success. These prevent closing phase 7 despite the green suite.

Only this report was written in the working tree; nothing was staged or committed. The pre-existing modified `docs/campaigns/phase7/scaffolding/campaign-summary.md` and untracked `docs/campaigns/phase7/scaffolding/gate-prompt-2.md` were left untouched. All probe patches were process-local. Python bytecode and pytest's cache provider were disabled.

## Executed checks

Required command, unmodified:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
4378 passed, 26 skipped in 321.30s (0:05:21)
```

Additional independent audit-hook runs, with `BRAVE_API_KEY=PHASE7_GATE_FAKE_KEY`:

```text
tests/test_web.py:                      455 passed in 22.01s
tests/test_web.py + tests/test_kick.py:  535 passed in 29.12s
```

The targeted A2 predicate parametrizations, actual UI-source test and AC24 profile-diff test also passed: **17 passed in 0.29s**. `git diff --check` passed. The summary's 109 cited test identifiers all collect; its 11 cited commits all exist.

Runtime remains Python 3.11.14 / aiohttp 3.14.3. The normal command sandbox failed to start with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; commands ran through the approved elevated path. Standalone adversarial rigs patched real connection and resolver entry points to raise, while preserving the fake transport/resolver and real executor.

## FINDINGS SETTLED

### F1 — HIGH — PARTIAL: the whole-call deadline still has gaps

The original `<p>` reproduction now returns `timeout timed_out` at approximately one second. Extraction, result construction and cache-record serialization run on a worker thread under the fetch's deadline. A separate instrumented run confirmed the parser raises `ExtractionAbandoned` and the worker stops. This is a substantial correction, but it does not establish the required bound for every permitted body or for the cache write.

All read probes below used `max_response_bytes=8388608`, `request_timeout_seconds=1`, `rig.handle._sleeper=None`, the real executor and fake public transport. Cache TTL was zero except for the two cache probes. Elapsed values are wall-clock seconds around `await rig.read(...)`.

| Executed case | Elapsed (s) | Status / evidence |
|---|---:|---|
| Original `b'<p>' * (8388608 // 3)` | 1.008545 | `timeout timed_out`; no cache entry. Previously 3.908 s / `success`. |
| Same body, observing cooperative cancellation | 1.007966 | `timeout timed_out`; worker stopped, observed exactly `['abandoned']`. |
| Never-answering body control | 1.001703 | `timeout timed_out`; no cache entry. |
| Short `<p>ok</p>` control | 0.000875 | `success`, text `ok`. |
| One unfinished tag: `b'<p ' + b'a' * (8388608 - 3)` | 0.105821 | `success`; within the deadline. |
| One unfinished quoted attribute: `b'<p x="' + b'a' * (8388608 - 6)` | 0.049254 | `success`; within the deadline. |
| One unfinished tag with many attributes: `b'<p' + b' a' * ((8388608 - 2) // 2)` | **2.358819** | **`timeout timed_out`, but more than twice the configured time.** Unmodified implementation. |
| Repeat of the many-attribute unfinished tag | **2.353311** | Same late `timeout timed_out`; unmodified implementation. |
| Requested numeric reference through the executor, `max_text_bytes=65536` | 0.002086 | `success`; exact text is `'a' * 65496 + 'A'` (65,497 bytes). |
| Cache-record serialization delayed 1.2 s in memory | 1.001706 | `timeout timed_out`; cache remains `(0 entries, 0 bytes)`. |
| Actual cache insertion delayed 1.2 s in memory | **1.201056** | **`success`; one entry, 509 bytes, stored after the deadline.** |

The numeric-reference body was exactly `b'a' * 65496 + b'&#' + b'0' * 40 + b'65;'`. Direct `extract_text(body, 'text/html')` also returned the exact expected string in 0.001160 s. Thus the requested feed-boundary regression is fixed, rather than merely truncated out of the result.

The unfinished-tag reproduction needs no monkeypatch to parsing or timing:

```python
rig = await t.read_rig(max_response_bytes=8388608,
                       request_timeout_seconds=1, cache_ttl_seconds=0)
rig.handle._sleeper = None
body = b'<p' + b' a' * ((8388608 - 2) // 2)  # 8,388,608 bytes, no closing >
rig.transport.script('example.org', t.html_page(body))
start = time.monotonic()
observation = await rig.read('https://example.org/')
print(time.monotonic() - start, observation.status, observation.error['code'])
```

At `modules/web/__init__.py:1050`, `check_for_whole_start_tag` runs before the new cooperative check. An unfinished tag returns before that check, while the growing buffered input is scanned again. Feed sizes double at line 861. Moving this work to a thread does not preempt the standard parser's long synchronous regex scan or ensure the event loop can deliver the one-second timer on time. The two executed overruns contradict the whole-call bound even though the final classification is now honest.

Cache serialization is protected; insertion is not. `_read` cancels its deadline at lines 2237–2238, then `_cached` calls `_cache.put` at line 2375. The cache-write probe used only this process-local wrapper around the original method:

```python
original_put = web._ObservationCache.put
def delayed_put(self, *args, **kwargs):
    time.sleep(1.2)
    return original_put(self, *args, **kwargs)
with patch.object(web._ObservationCache, 'put', delayed_put):
    # Normal short-page read; TTL enabled; one-second setting, real timer.
    observation = await rig.read('https://example.org/')
```

This is an injected scheduling/latency control, not a claim that ordinary insertion takes 1.2 seconds. It demonstrates the requested late-cache-write path is outside every remaining deadline and can still return and cache a late success. The separate serialization control delays `record_size` instead and correctly times out without storing anything.

Required remaining correction: preserve the same deadline through the cache write and terminal decision, and bound parser work even when `HTMLParser` retains one enormous unfinished construct. The search-side counterpart is recorded under N1 below.

### F2 — MEDIUM — RESOLVED: malformed items preserve their valid siblings

Re-executed the first report's exact JSON payloads through `search_rig` and the real executor, using `json.dumps(payload).encode()` as the fake response. The valid title was `Valid`; the invalid sibling's title was the decoded lone surrogate `\ud800`. Brave used an explicit fake API key.

| Provider / payload | Elapsed (s) | Observed result |
|---|---:|---|
| Wikipedia, valid + surrogate item | 0.000606 | `success`, exact valid article result, `result_count=1`, `dropped_count=1`. |
| Brave, valid + surrogate item | 0.000782 | `success`, exact valid result, `result_count=1`, `dropped_count=1`. |
| Wikipedia, surrogate item only | 0.000457 | `error malformed_response`, no result, `dropped_count=1`, no cache entry. |
| Brave, surrogate item only | 0.000442 | Same error, absent result, dropped count and empty cache. |
| Wikipedia, well-formed empty list | 0.000441 | `success`, zero results, zero dropped. |
| Brave, well-formed empty list | 0.000435 | `success`, zero results, zero dropped. |

`_parsed_items` now handles unusable items at the item boundary; an entirely unusable nonempty list becomes `Malformed(dropped=...)`. The new surrogate, malformed-item and all-malformed tests also passed in both web runs and the full suite.

### F3 — MEDIUM — RESOLVED: A1 authorizes AC30; A2 detects the comment

Amendment A1 adds exactly the specified seventh node. Against `7c29434`, AC30's executable changes are only catalog count **14 → 16** and the exact expected set gaining `web.search` and `web.read`. The exact ready/discovered sets, multiplicity assertions and chat scenario assertions remain. There is no inequality substitution or assertion removal. A1 changes the allowlist, not R1–R10 or AC1–AC26.

Executed the test's own `_web_mentions` predicate directly:

```text
input:  # Enable web when the operator grants its actions\nx = 1\n
output: ['# Enable web when the operator grants its actions']

input:  from aiohttp import web\nresponse = web.Response(text='ok')\n
output: []
```

The predicate now checks comments/string tokens for a bare `web` and recognizes the aiohttp binding/member uses. The actual A2 source test and all 15 new predicate cases passed. The UI implementation's only change is the docstring phrase `web page` → `HTML page`; runtime behavior is unchanged.

The AST audit still detects A2 as a changed assertion body outside the seven catalog/guide adaptations. Its current change strengthens module detection and implements this gate's explicitly requested precision repair; it is not another changed count expectation or an unresolved loosening. The seven allowlisted expectation adaptations remain exact.

### F4 — MEDIUM — RESOLVED: the three skip paths are gone

An AST scan of the entire current `tests/test_web.py` found **zero** `pytest.skip`, `pytest.xfail`, `pytest.importorskip`, or skip/xfail marker calls. Comparing every changed test file to `7c29434` found no added such calls or deleted pre-existing test functions. The local JSON-scanner helper named `skip` is not a pytest skip.

AC24 now asserts that git, the work tree and base commit exist, then executes the empty profile-diff check. Its targeted run passed unconditionally here, as did both web runs and the full suite. CI now requests `fetch-depth: 0`, consistent with treating missing history as failure.

### F5 — MEDIUM — RESOLVED: the guard self-test no longer constructs a socket

The independent `sys.addaudithook` observes socket operations below the monkeypatched methods. With the fake Brave key set, the entire web file produced:

```text
455 passed in 22.01s
socket.__new__: AF_UNIX=536; AF_INET=0; AF_INET6=0
socket.connect=0; socket.bind=0
socket.getaddrinfo=1: ('127.0.0.1', 1, 0, 0, 0)
```

The AF_UNIX constructors are asyncio's event-loop socket pairs, already present in the first pass (500 then); this is not a claim that pytest creates zero kernel sockets. No explicit phase-owned socket constructor remains in the self-test: `_UnopenedSocket` invokes the patched methods on a stand-in, which is refused before an OS call. No provider connection occurs.

The combined web/Kick audit produced **535 passed in 29.12s**, with AF_UNIX=652, AF_INET=160, 103 binds, 29 connects and the same single numeric-loopback lookup. The recorded binds/connects were to `127.0.0.1`, from the existing Kick harness. `tests/test_kick.py` and its shared harness are unchanged against the phase base. These existing loopback connections are not a phase-7 regression. F5's new self-test socket is removed; no socket family was substituted for it.

## Whole-branch re-check

The first report's detailed R1–R10 and AC1–AC26 test map still applies. All its cited web tests ran again; only these assessments change or remain blocked:

| Requirement / criteria | Second-pass assessment |
|---|---|
| R1 / AC1–AC4 | Still covered and passing; module/contracts, key-free default, grants and no-network preparation unchanged. |
| R2 / AC5–AC6 | Now passes the F2 sibling/all-malformed counterexamples as well as the original fixtures. |
| R3 / AC7–AC8 | Original content tests pass; added charset, hidden-state, UTF-8 and numeric-reference controls pass. Deadline limitations are under R4–R6. |
| R4 / AC9–AC13 | Address, rebinding, redirect, allowlist, proxy and streamed-byte tests pass. Whole-call timing still fails F1/N1 despite green AC13 fixtures. |
| R5 / AC14–AC17 | Settings, admission and cache bounds pass. Cache-record serialization is now timed; insertion and whole-call completion are not fully covered by the deadline. |
| R6 / AC18 | Original outcome rows pass; unfinished-tag read now reports timeout honestly, but N1 still reports overdue search success. |
| R7 / AC19–AC20 | Still passes both-provider success/refusal/error/timeout/debug scans and their leak-detection controls. No new trace/log surface was added by the fixes. |
| R8 / AC21–AC22 | Still passes the actual brain-rendered forged-marker/delimiter page and ungranted scene-action attempt: data stays framed, tools stay unchanged, action is refused. |
| R9 / AC23–AC24 | Grant/revocation and executable guide fragments still pass. Dependencies/profiles unchanged; AC24 has no skip path. |
| R10 / AC25–AC26 | AC25's fake-provider/manual-trial portions pass and F5 is resolved under the clarified invariant. All 109 summary test IDs collect and 11 commits exist. AC26 remains blocked by this verdict; its ledger also needs N2's refresh. |

Thus all 26 criteria retain their mapped coverage (AC22 shares AC21's scenario; AC26 is the manual gate). Green fixtures do not override the executed timing counterexamples. The first pass's mutation-sensitivity matrix was not rerun wholesale; its security scenarios and negative-control tests did run again.

The pre-existing test audit found no deletion, new skip/xfail or loosened expectation. Seven allowlisted nodes have the mechanical constant/set adaptations, including the bilingual guide's shared `GUIDES` constant and A1's AC30 node. A2's additional predicate repair is accounted for under F3. No other existing test files changed. Since the first reviewed head, test edits are confined to `test_web.py` and `test_config_ui.py`.

`git diff 7c29434 -- pyproject.toml` remains exactly the `"modules.web" = ["module.yaml"]` package-data line. Runtime dependencies remain exactly `aiohttp>=3.9,<4` and `PyYAML>=6,<7`. `git diff 7c29434 -- '*.example'` is empty. The brain, action executor and loader remain unchanged; the sole core-file change is the UI docstring noted above.

Both guides still prescribe a manual Wikipedia/optional-Brave trial and its separate `docs/campaigns/phase7/web-trial-record.md`. Both retain the revocable two-rule grant and deletion/restart procedure. The English guide remains normative and the French guide defers to it; their fragment/settings/documentation tests pass.

## REGRESSIONS

No newly introduced runtime regression was demonstrated in the three fix steps. The unmodified unfinished-tag overrun and unprotected cache insertion are remaining F1 gaps, not evidence that the original `<p>` fix regressed. N1 is an additional pre-existing sibling path exposed by the whole-branch re-check. N2 is a stale closing-evidence document after the fixes. The full suite has no new failure or newly added skip.

## NEW FINDINGS

### N1 — HIGH: `web.search` still returns success after its configured deadline

Location: `modules/web/__init__.py:2404`. Search still parses synchronously after `fetch` has canceled its timer. The new cooperative parser checks have no expiry context on this path. A bounded, correctly shaped Wikipedia answer with one large markup excerpt returned **`success` after 3.568903 s** with `request_timeout_seconds=1`, `result_count=1`, `dropped_count=0`. No parser/timing implementation was monkeypatched.

Executed reproduction, using the same network-forbidden harness:

```python
rig = await t.search_rig(max_response_bytes=8388608,
                         request_timeout_seconds=1, cache_ttl_seconds=0)
rig.handle._sleeper = None
body = json.dumps({'query': {'search': [
    {'title': 'Valid', 'snippet': '<p>' * 2000000},
]}}).encode()  # 6,000,058 bytes, below the configured response bound
rig.transport.script('en.wikipedia.org', t.FakeResponse.body(
    body, headers={'Content-Type': 'application/json'}))
start = time.monotonic()
observation = await rig.search('q')
print(time.monotonic() - start, observation.status)
```

This is the search-side counterpart of F1, rather than a separate security mechanism: the same whole-call deadline must also cover backend parsing, result construction and cache processing. R4/R5/R6 apply to both actions.

### N2 — LOW: the phase summary's closing evidence was not refreshed

Concrete contradictions remain in `docs/campaigns/phase7/summary.md`: lines 22–23 and 199–202 still describe three existing skip paths, whereas the executed AST scan finds zero; lines 220–227 still call AC30 unallowlisted and say a bare `web` comment is not caught, whereas A1 authorizes AC30 and the actual predicate catches the exact comment. Its evidence head is still `174ab51`; its ledger does not cite the three fix steps, contrary to the plan's requirement to refresh the summary before the next gate.

The historical suite count is labeled with its old evidence head, so it is not itself a false claim about this run. Refresh the current findings/allowlist audit and fix-step evidence so the closing summary describes the reviewed tree. This report cannot update that other file under the user's one-file authorization.

## NOT VERIFIED

- No real Wikipedia or Brave call was made. Credentials, live API behavior, current upstream responses, TLS/SNI and pinning over an actual remote socket remain unverified; fake/recording sessions do not replace the manual trial.
- No hand-written operator trial record was produced. Its absence remains permitted by R10; live-service success is not claimed.
- The installed-distribution smoke test remains an environmental skip (this virtualenv has no pip). Direct manifest/package-data assertions ran; installed behavior is not established by this run.
- The first pass did not demonstrate unbounded decompression in installed aiohttp 3.14.3. Other supported aiohttp/Python versions and network-specific translated-address behavior remain untested; the earlier NAT64/6to4 observations do not establish behavior across versions or gateways.
- The changed CI checkout configuration was inspected but not executed on a hosted CI runner. The current full-history checkout satisfies AC24 locally.

REQUEST_CHANGES
