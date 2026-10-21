# P17F1 — The hook bound is hard, and the listening path transcribes nothing before a decision

Two of the closing gate's three findings. Both are small and precise; neither changes a design decision.

## F1 — the promised hard bound is not hard

`core/triggers.py:1328`: the hook and the timeout share one event loop (`ensure_future` + `asyncio.wait`).
The post-completion timestamp check rejects a late answer, but cannot pre-empt a hook that never yields.

Executed by the gate: with `hook_timeout_seconds=0.01`, a hook that busy-waits 0.12 s made
`await engine.evaluate_async(...)` take **0.1202 s**, then returned `accepted=False,
reason='rejected:wake_hook_timeout'`. The classification is right; the bound is not.

Brief decision 2 says **hard timeout** — that is binding. Required: run the hook where the engine can
abandon it — a worker thread with its own loop, bounded by the remaining budget, its answer discarded if it
arrives late — so the decision completes within the bound whatever the hook does, and a hook that blocks
does not stall the event loop. Keep the current fail-closed semantics: a late, raising or abandoned hook is
a **hard veto**, never an acceptance, and never inside a combination.

Test (fails on the current tree): the 0.12 s busy-wait hook with a 0.01 s bound returns in ≈0.01 s (assert
elapsed under a small margin), with no trigger; an independent `loop.call_later(0.02, ...)` heartbeat fires
during the call, proving the loop was not blocked; the failing-veto composition test still passes.

## F2 — the listening path transcribes at startup, and the tests filtered it out

`modules/audio_input/__init__.py:2034` calls `_probe_transcription()` during prepare whenever transcription
is enabled — an 8044-byte, 0.25 s silence WAV — and `tests/test_audio_input.py:1599` defines
`call_requests()` by destructuring `probe, *rest = transport.requests`, so the tests counted only the
requests **after** the probe. Counted without that filter, the gate measured one call before
`start_inputs` and, for a declined candidate, one call with zero acquisitions.

That defeats the phase's own claim: no transcription before an accepted decision. Required:
- the listening path performs **no** transcription probe — the probe, if it must exist at all, belongs to
  the capture action and must not run when the module is only listening;
- the listening ACs assert the **unfiltered** transport request total (AC26's `assert seen == (0, 0)` and
  AC37's empty-list assertion must hold against `list(transport.requests)` with no slicing);
- a detector that never fires produces zero transcription requests; a declined candidate produces zero;
  only an accepted candidate produces exactly one.

## Constraints

- `core/triggers.py`, `modules/audio_input/__init__.py`, `tests/test_declared_triggers.py`,
  `tests/test_audio_input.py` only.
- No new dependency; no test skipped, xfailed, deleted or loosened; full suite green with
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- Reply with the measured elapsed time of the blocking-hook probe, the unfiltered counts, the test names,
  and the full-suite count.
