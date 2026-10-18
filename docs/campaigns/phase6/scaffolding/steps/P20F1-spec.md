# Step P20F1 — the agent's retained call cache checks the call identity before serving it

Gate fix step of Phase 6. It answers the closing gate's **first pass**
(`docs/campaigns/phase6/full-branch-review.md`), which refused approval with 29 findings closed and five
security-boundary findings open.

## The finding (verbatim)

- **F2 OPEN** — AC6/AC7 fresh-call tests pass, but replay probe A sends the retained `(id, seq)` with an out-of-set action and another principal and receives **`success`, identical to the original observation**, rather than a refusal. The agent cache precedes both new checks. Baseline fresh-call regressions: **3 failed**.

- **F3 OPEN** — `test_actions.py::test_ac3_a_reused_call_id_naming_another_call_is_refused` (five identity components), `::test_ac3_a_reuse_whose_arguments_only_share_a_repr_is_refused`, and `::test_ac3_an_identical_reuse_the_policy_now_refuses_is_refused` pass at the executor; baseline **7 failed**. The same observation-before-authorization defect survives in agent replay probe A: an ungranted principal reads the retained success without entering the executor.

### A — agent replay bypasses the pairing checks (F2/F3)

Executed with the in-tree fake peer and real executor:

```python
import asyncio, sys
sys.path.insert(0, 'tests')
import test_proxy as t

async def probe():
    host, brain, recorder = await t.recorded_host()
    try:
        await brain.call('gate-replay', 1)
        original = await brain.observation('gate-replay')
        await brain.call('gate-replay', 1, principal='ungranted-attacker',
                         action=t.FAKE_WRITE, arguments={'text': 'changed'})
        forged = await brain.observation('gate-replay')
        print(original['status'], forged['status'],
              forged == original, len(recorder.calls))
    finally:
        await host.close()

asyncio.run(probe())
```

Observed: **`success success True 1`**. The second request is outside the negotiated set and names an ungranted principal, yet reads the first request's observation. `AgentLinkModule._on_call` returns the retained frame on `(id, seq)` before `_action_call`, the negotiated-action check and the pairing-principal check. The fixed executor cannot protect a call that this sibling cache never forwards. Exact retries must still work; altered retries must be checked against an immutable identity before retained results or waiter attachment are exposed.

## Required resolution

`AgentLinkModule._on_call` serves a retained `(id, seq)` observation before `_action_call`, the
negotiated-action check and the pairing-principal check run, so an altered retry — an out-of-set action,
an ungranted principal, changed arguments — reads the first call's `success`. Probe A observes exactly
that: `success success True 1`.

Required: the retained observation is served only when the retry is the SAME logical call — the same
negotiated action, the same pairing-fixed principal, the same destination and the same arguments — and
only after those checks have run for the incoming frame. Any other reuse is refused with a terminal
status, without exposing the retained observation and without reaching the executor. An exact retry must
still return the retained observation (that is the point of the cache). The identity used for the
comparison must be computed from the call, not from a rendering of it (the executor's own replay rule in
`core/actions.py` is the reference: same identity components, compared structurally).

## Files

[`modules/agent_link/__init__.py`, `tests/test_proxy.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing it.

## Tests

Probe A becomes a regression: the first call is granted and in-set; the second, with the same
`(id, seq)` but an out-of-set action, another principal, and changed arguments, is refused, the executor
sees exactly one invocation, and the returned observation is not the retained one. An exact retry returns
the retained observation with one execution. A retry whose action is in-set but whose arguments differ
only by a rendering that used to collide (a dict order, a tuple vs list) is refused too.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Never the system python: it lacks aiohttp. The suite must not drop below 3,849 passed / 26 skipped.

## Authority

- The closing gate's first pass, whose findings below are quoted verbatim with their executed probes:
  `docs/campaigns/phase6/full-branch-review.md` (authoritative for this step).
- The original review: `docs/reviews/2026-10-16-full-adversarial-review.md`.
- Brief, specification, plan: `docs/campaigns/phase6/brief.md`, `spec.md`, `plan.md`.
- The phase's amendment record: `docs/campaigns/phase6/scaffolding/plan-amendments.md`.
- The pre-fix tree is `8010edc`; the phase head is the current `main`.
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon`. Working tree at `main`.

## Standing phase-6 decisions

- Phase-5 decisions stay binding: optional bridge, replacement not parallel, honest outcomes,
  default-deny, single-owner inputs, the bridge "not delivered".
- Close exactly the finding(s) this step names. If a sibling path shows the same defect, report it in
  your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. Assertions may be replaced only for a test named in
  the specification's failure allowlist, with a docstring citing its finding and requirement; elsewhere
  an assertion may only be EXTENDED (a new check on top), never reduced.
- The full suite is green at every step: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit
  (`fix(phase6): P20Fn — ...`). The scope MUST be `phase6`.
- Never weaken, skip, xfail or delete an assertion. If an existing assertion encodes the defect, replace
  it only as the specification allows and cite finding + requirement in the docstring.
- No new runtime dependency; no vendor or model name in code or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do.
- Dependencies: P20F1..P20Fn land in order; each spec assumes the previous ones are on `main`.
