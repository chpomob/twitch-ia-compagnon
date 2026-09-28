commit: fix(phase4): P19F2 — honest boolean tri-state, bounded dispatch queue, bounded post-kill wait

# Step P19F2 — gate findings F3, F4 and F5 (the three MEDIUM defects)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 closing gate
report `docs/campaigns/phase4/scaffolding/gate-report-1.md` — read **F3, F4 and F5 in full**, including
their executed counterexamples, and the approved specification (`docs/campaigns/phase4/spec.md`).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (project virtualenv).
The suite must stay green and must not drop below 2,889 passed / 18 skipped.

## F3 — MEDIUM — false boolean drafts vanish when the setting is absent and defaults true

When `synthesis.probe` is omitted from the configuration, the runtime's `_Settings.from_mapping()` sets
it to **True** and the manifest declares `default: true`, but the generated control renders unchecked,
so an edit to `false` is not distinguishable from "not set" and disappears. The gate's counterexample
prints `unchecked=True edits=[] runtime_probe=True`. Locations: `core/config_ui/__init__.py:1585`,
`:2490`; `modules/audio_output/__init__.py:510`.

Required: the generated boolean control must render the **effective** value, and a draft must express
all three states distinctly — unset (inherit), explicitly false, explicitly true — so that an operator
setting `false` over a `default: true` produces a draft that actually writes `false` to the overlay.
Turn the counterexample into a running test through the shipped manifest, the parser and the runtime's
settings parser.

## F4 — MEDIUM — four workers do not bound queued HTTP work

`catch_all` reads each request body and `_dispatch` unconditionally enqueues the work in a
`ThreadPoolExecutor`'s unbounded queue, with the Host/session/CSRF checks running only later. Locations:
`core/config_ui/__init__.py:3986`, `:3993`.

Required: queued work is bounded, and a request that cannot be admitted is refused **honestly** (a named
status, no work silently dropped, no unbounded memory growth) — while the request body read itself must
not be able to grow without limit either. Cheapest correct shape: a bounded queue or a semaphore taken
before enqueuing, with the refusal path covered by a test that saturates the bridge as the gate's
counterexample does.

## F5 — MEDIUM — Apply's stop path has an unlimited post-kill wait

`Supervisor.stop` waits a bounded time for termination, then kills the child and calls `child.wait()`
with **no timeout**, so a child that ignores SIGKILL-equivalent conditions hangs the UI forever.
Location: `core/config_ui/__init__.py:3785`.

Required: every wait in the stop/restart sequence is bounded, and an unkillable child ends in an explicit,
reported outcome rather than an indefinite hang; the restart path's existing 60-second accounting stays
consistent with it. Cover the gate's counterexample (a child whose first wait times out) with a test that
observes the sequence `terminate`, bounded wait, `kill`, bounded wait, and an honest report.

## Constraints

- Fix the production code; never weaken an existing assertion. If a test encodes the defective
  behaviour, invert it and cite the finding in its docstring.
- No new runtime dependency; no positive-duration sleeps in tests — inject the clock.
- One atomic commit for the whole step; the message is pinned at the top of this file.
- Report: for each finding, the `file:line` changed, the bound or state shape you settled, the new tests,
  and the exact suite counts before and after.
