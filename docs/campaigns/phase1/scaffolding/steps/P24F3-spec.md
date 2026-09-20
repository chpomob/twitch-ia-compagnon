# Step P24F3 — gate pass-2 finding F6 (duplicate upload id overwrites a live call's reference)
commit: fix(phase1): P24F3 — refuse a duplicate upload id that would steal a live call's reference

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Gate pass 2
(`docs/campaigns/phase1/scaffolding/gate-report-2.md`) resolved F1–F5 and raised exactly ONE new
finding, F6. This step fixes F6 only.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`.

## F6 — P2 — Duplicate upload ID overwrites another live call's acknowledged reference

`modules/proxy/__init__.py:1660` unconditionally replaces the mapping; there is no collision rejection
at `:1543–1574` or `:1640–1661`. `docs/proxy-protocol.md:194` requires an identifier UNIQUE within the
session.

Executed counterexample (the gate ran this and it passed today):
1. On one paired connection, call A uploads `same-id` with bytes `AAAAAAAA` → accepted ack.
2. While A is still live, call B uploads `same-id` with `BBBBBBBB` → accepted ack, REPLACING A's mapping.
3. A's observation now fails with `error attachment_refused` (its accepted id now belongs to B); B
   succeeds. A's original bytes remain stored but its acknowledged reference no longer resolves.

Reproducer to adapt as a test (from the gate report, `X1`):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import asyncio, sys
sys.path.insert(0, 'tests')
from test_proxy import Brain, capture_call, capture_result, image_ref
async def duplicate_id():
    brain = Brain()
    try:
        await brain.activate()
        agent = brain.connect()
        await agent.pair_up()
        a = asyncio.create_task(brain.executor.invoke(capture_call('c-a', run_id='run-A', deadline=brain.clock()+30)))
        await agent.call_frame()
        b = asyncio.create_task(brain.executor.invoke(capture_call('c-b', run_id='run-B', deadline=brain.clock()+30)))
        await agent.call_frame()
        ack_a = await agent.transfer('same-id', b'AAAAAAAA', call_id='c-a')
        ref_a = brain.module._session.acknowledged['same-id'].ref
        ack_b = await agent.transfer('same-id', b'BBBBBBBB', call_id='c-b')
        await agent.observe('c-a', result=capture_result(8), parts=[image_ref('same-id',8)])
        obs_a = await a
        await agent.observe('c-b', result=capture_result(8), parts=[image_ref('same-id',8)])
        obs_b = await b
        assert ack_a['accepted'] and ack_b['accepted']
        assert obs_a.status == 'error' and obs_a.error['code'] == 'attachment_refused'
        assert brain.store.get(ref_a) == b'AAAAAAAA'
        assert obs_b.status == 'success'
    finally:
        await brain.close()
asyncio.run(duplicate_id())
PY
```

Required fix: **reject a collision with a LIVE acknowledged id before allocating or replacing its
translation**, and preserve the original entry and bytes (the duplicate upload is refused with the
protocol's refusal outcome, and the original owner's reference keeps resolving). Keep the identity
protection BOUNDED — do not add an unbounded session history: reuse the existing 256-entry ceiling and
its counted saturation policy.

Tests to add (all through the real proxy boundary):
- cross-call duplicate: B's duplicate id is refused; A's reference still resolves and A's observation
  succeeds with its own bytes; B's call fails cleanly (no partial effect, no lease).
- same-call duplicate upload: the second upload with the same id is refused and does not replace the
  first one's bytes/reference.
- the translation table stays bounded across repeated duplicate attempts.

## Constraints
- Fix the PRODUCTION code; never weaken, skip, xfail or delete an unrelated assertion.
- Scope: `modules/proxy/__init__.py`, `docs/proxy-protocol.md` (only if the refusal outcome for a
  duplicate id must be stated), `tests/test_proxy.py`.
- One atomic commit; the message is pinned above.
- Report: the `file:line` changed, the test that now enforces it, anything not fixed + why.
