# P12F4 — The whole-call deadline covers every path, and the summary tells the truth

The second gate pass settled F2–F5 and left three things open: F1 is PARTIAL, N1 is a new HIGH, N2 is a
LOW about evidence. This step closes all three.

## F1 — remaining gaps in `web.read`

The `<p>` reproduction is fixed (`timeout timed_out` at 1.008 s). Two paths still escape:

**(a) One enormous unfinished construct.** At `modules/web/__init__.py:1050`,
`check_for_whole_start_tag` runs before the cooperative deadline check, and an unfinished tag returns
before it, with the growing buffer scanned again (feed sizes double at line 861). Executed, unmodified:

```python
body = b'<p' + b' a' * ((8388608 - 2) // 2)   # 8 MiB, no closing '>'
# request_timeout_seconds=1 → timeout timed_out at 2.358819 s (and 2.353311 s on repeat)
```
The classification is honest but late: the configured bound is not honoured. Bound the parser's work
itself — a check inside the goahead loop, before the whole-start-tag scan, and a cap on how large a single
buffered construct may grow (abandon beyond it) — so no body can make one iteration exceed the budget.

**(b) Cache insertion after the deadline.** `_read` cancels its deadline at lines 2237–2238, then
`_cached` calls `_cache.put` at line 2375. With insertion delayed 1.2 s in memory the call returned
`success` at 1.201 s and stored one entry. The write must be under the same deadline and a late success
must not be stored: either perform the insertion inside the bounded section or check the remaining budget
after it and classify `timed_out` without an entry.

## N1 — `web.search` has the same hole

`modules/web/__init__.py:2404`: search parses synchronously after `fetch` cancelled its timer, and the
cooperative parser checks have no expiry context there. Executed with a bounded, correctly shaped answer:

```python
body = json.dumps({'query': {'search': [{'title': 'Valid', 'snippet': '<p>' * 2000000}]}}).encode()
# 6,000,058 bytes, request_timeout_seconds=1 → success at 3.568903 s, result_count=1
```
Generalise the read-side discipline to the search path: backend JSON parsing, per-item result
construction, the dropped-count accounting and the cache write all run under the same whole-call deadline,
on a worker thread, with the same honest `timed_out` classification. R4/R5/R6 apply to both actions.

## N2 — the summary must describe the reviewed tree

`docs/campaigns/phase7/summary.md` still contradicts the tree: lines 22–23 and 199–202 describe three
skip paths that no longer exist (the AST scan finds zero); lines 220–227 call AC30 unallowlisted and say a
bare `web` comment is not caught, while Amendment A1 authorizes AC30 and the predicate does catch that
comment; its evidence head is `174ab51` and it cites none of the fix steps. Refresh the current
findings/allowlist audit and add the fix-step evidence, so the closing summary describes the tree this
gate reviews.

## Tests (each fails on the current tree, passes here)

- **F1a**: the 8 MiB unfinished-tag body returns `timeout timed_out` at ≈1 s (assert elapsed under a small
  margin), plus the many-attribute variant; and a legitimate 1 MiB page of normal tags still reads.
- **F1b**: with cache insertion delayed past the deadline, the call is `timed_out` and the cache holds
  no entry.
- **N1**: the 6 MB search payload returns `timeout timed_out` at ≈1 s; a normal search inside the budget
  still returns its results; a search whose parsing fits the budget is unaffected.
- No regression: the existing deadline, byte-bound, cache and search tests keep passing unchanged.

## Constraints

- `modules/web/__init__.py`, `tests/test_web.py`, `docs/campaigns/phase7/summary.md`.
- No new dependency; no test skipped, xfailed, deleted or loosened. Full suite green with
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- Reply with what changed, the measured elapsed times for the three reproductions, the test names, and
  the full-suite count.
