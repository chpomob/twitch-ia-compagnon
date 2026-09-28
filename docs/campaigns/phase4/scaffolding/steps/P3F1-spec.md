commit: fix(phase4): P3F1 — restore the presence-pack end-to-end scenario after the overlay merge

# Step P3F1 — regression fix: the presence-pack scenario broke when the runtime started merging the overlay

Plan step `P3` (runtime merges the overlay; `--overlay` CLI) was merged on `main` while one existing
test was RED. The campaign halted for that reason (this is a fix round, not a plan step).

Authority documents (read them, do not re-derive):
- Approved specification: `docs/campaigns/phase4/spec.md` (R2 and the overlay/merge criteria)
- Approved plan: `docs/campaigns/phase4/plan.md` (P2 and P3 describe what the overlay work must do)
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules).
Working tree at `main`.

## The failure, reproduced

```
.venv/bin/python -m pytest tests/test_presence_pack.py::test_ac6_the_presence_pack_runs_every_observation_row -q
E  AssertionError: condition did not become true within the turn budget
tests/conftest.py:2220: AssertionError
1 failed
```

The full suite is otherwise green: `1 failed, 2517 passed, 18 skipped`. That test is the phase-3
end-to-end scenario of the shipped presence profile: with a scripted model and fake transports it
starts the profile, reaches readiness, then observes one row at a time (raid → `chat.write` +
`audio.play`; `!missed` → `chat.read`; `!brb` by the broadcaster → `stream.scene.set`; `!clip` by a
moderator → `stream.clip.create`; a viewer's `!brb` → 0 scene calls; a second visit → `memory.recall`
with `known: true`; the broadcaster's `!watch` → one `watch.state active` fact). A scripted model
consumes a fixed sequence, so an extra or missing step anywhere upstream desynchronises it and the
turn budget expires.

## What to do

1. **Find the root cause first** — do not patch the test to fit the behaviour. Determine how the
   overlay merge changed what that scenario sees: an implicit overlay path being derived and merged
   where none existed, a merge that alters the effective profile, an extra read, an extra or missing
   await, or a changed activation order are all candidates. State the cause in one sentence in your
   report.
2. Then fix it at the right layer, with the invariant the specification wants: **with no overlay
   file present, the runtime's behaviour must be byte-for-byte what it was before the overlay work**
   (same effective configuration, same work performed, same order). If a derived overlay path can
   pick up a file that is not an overlay, the derivation or the read is wrong — fix that, not the
   test.
3. Keep every other overlay guarantee from P2/P3 (explicit `--overlay`, documented precedence,
   removal of an override, `--check-config` honouring the overlay) and its tests green.
4. If — and only if — the failing test encodes a guarantee the approved specification has
   superseded, replace that test's encoded expectation and cite the superseding requirement or
   acceptance criterion in its docstring. Do not weaken, skip, xfail or delete an assertion
   otherwise.

## Files

Determine the root cause, then touch only what the fix genuinely needs — expected to be among
`core/main.py`, `core/overlay.py`, `tests/test_overlay.py`, `tests/test_presence_pack.py`,
`tests/conftest.py`. If you believe another file must change, stop and report it instead of editing
it.

## Tests

- The named failing test must pass, and it must still fail without your change (verify by reasoning
  about the mechanism, or by temporarily reverting the production part locally before the commit).
- `tests/test_overlay.py` and every test the overlay work added must stay green.
- Full command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` — the suite
  must not drop below **2517 passed, 18 skipped**, and must be fully green.

## Constraints

- One atomic commit; the message is pinned at the top of this file (read it, use it verbatim).
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: the root cause in one sentence, what changed, the exact test counts before and
  after, and anything you could not determine.
