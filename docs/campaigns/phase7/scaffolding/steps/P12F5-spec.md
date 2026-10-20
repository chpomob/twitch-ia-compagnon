# P12F5 — The terminal answer does not wait for the cache, and the summary's numbers are current

The third gate pass resolved F1a (the 8 MiB unfinished tag now returns in 0.77 s) and N1 (the 6 MB
Wikipedia answer times out at 1.001 s). Two items remain, and the first is what the gate calls the last
unbounded scheduling path.

## F1b — cache insertion still blocks the terminal answer

`modules/web/__init__.py:2655`: `_cached` calls `_cache.put(...)` synchronously on the event loop. The
clock check just after it (lines 2656–2659) correctly discards a late entry and reports `timed_out`, but
the call cannot return until the store finishes. Executed with the gate's wrapper:

```python
original_put = web._ObservationCache.put
def delayed_put(self, *args, **kwargs):
    time.sleep(1.2)
    return original_put(self, *args, **kwargs)
with patch.object(web._ObservationCache, 'put', delayed_put):
    # request_timeout_seconds=1, short page, real timer
    observation = await rig.read('https://example.org/')   # timed_out at 1.201051 s
```
Independent `loop.call_later(1, ...)` heartbeats fired at 1.201245 s and 1.201417 s on the read and search
probes: the loop really is blocked until the store returns. The serialization control, by contrast,
returned at 1.001841 s — serialization is raced, insertion is not.

Required: the terminal answer is delivered within the configured whole-call time, whatever the store
does, and **an abandoned insertion must never land**. Do the insertion off the event loop under the
call's remaining budget, and make the store itself refuse to commit once the call has given up (a
deadline/token checked inside the cache's own lock before it mutates). The outcome stays honest: a call
that could not store in time is `timed_out`, and the cache is unchanged.

**Also required**: the delayed-store regression must assert **elapsed time**, not only status, cache
contents and trace. Today it would pass while the call took 1.2 s. Measure wall time around the call and
assert it stays within a small margin of the configured setting.

Test (fails on the current tree): with the 1.2 s insertion wrapper and a 1 s setting, the call returns in
≈1 s, status `timed_out`, cache `(0 entries, 0 bytes)`; and after another 0.5 s the cache is still empty
(the abandoned store did not land). The undelayed path still stores and still answers normally.

## N2 — three stale numbers in the phase summary

`docs/campaigns/phase7/summary.md` still carries figures from before P12F4 and asserts them as current.
Executed at the current head:

| Where | Says | Actual |
|---|---|---|
| line 188 | 467 web tests | **474** (current-head `tests/test_web.py` run) |
| line 227 | `+6,917 / −15` test lines, `test_web.py +6,246` | **15 files, +7,005 / −15**, `test_web.py **+6,334**` |
| line 177 | 4,390 passed / 26 skipped at P12F4 | **4,397 passed / 26 skipped** |

Refresh exactly those. The rest of the summary's historical discussion is correct and stays.

## Constraints

- `modules/web/__init__.py`, `tests/test_web.py`, `docs/campaigns/phase7/summary.md`.
- No new dependency; no test skipped, xfailed, deleted or loosened; nothing else changes.
- Full suite green: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- Reply with the measured elapsed time of the delayed-store probe, the test names, the three corrected
  figures, and the full-suite count.
