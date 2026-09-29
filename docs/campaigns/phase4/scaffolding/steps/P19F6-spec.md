commit: fix(phase4): P19F6 — Check honours the layout digest instead of retargeting positional fields

# Step P19F6 — gate-3 finding N2 (MEDIUM): Check silently retargets positional fields

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 3 report
`docs/campaigns/phase4/scaffolding/gate-report-3.md` — read **N2** in full, including the executed
D/stale reproduction — plus the approved specification (`docs/campaigns/phase4/spec.md`, the Check
criteria and the stale-submission rules).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must stay
green and must not drop below **2,948 passed / 18 skipped**.

## The defect

Replacing raw-key form names with positions (`triggers.twitch.channels.@0.combination`) removed a real
disclosure, but the **layout guard was added only to Save and Remove**. `Check` parses a submission
without verifying that the page it came from still matches the current layout, so after a base-key
reorder the fields are silently retargeted: the gate rendered channels in the order `first`, `second`
(both `all_of`), edited `first`, and `POST /check` returned **200** after calling the checker with

```python
{'triggers': {'twitch': {'channels': {'second': {'combination': 'any_of', 'rules': []}}}}}
```

while the identical submission to `/save` correctly returned **409 stale**. A readiness or diagnostics
verdict computed against the wrong key is worse than no verdict: the operator is told their edit is fine
when it was never looked at.

## Required

`Check` must apply the **same layout guard** as Save and Remove: the layout digest (`:1403` and whatever
represents it in the submitted form) is verified before the draft is parsed into configuration, and a
submission whose layout no longer matches is refused with the same explicit stale outcome the Save route
already uses — never parsed, never silently retargeted, never reported as a readiness verdict for a key
the operator did not edit. Keep the positional naming and the disclosure fix intact, keep the legitimate
`Check` path working for a submission whose layout matches, and make the refusal honest in the response
the UI receives (the same shape Save uses, so the page can tell the operator to reload).

Cover the gate's reproduction with a test: build a page, reorder the base keys, submit the old layout to
Check and to Save, and assert both refuse identically, with the checker never receiving a retargeted
draft.

## Constraints

- Fix the production code; never weaken an existing assertion. If a test encodes the defective
  behaviour, invert it and cite the finding in its docstring.
- One atomic commit; the message is pinned at the top of this file.
- Report: the `file:line` changed, where the guard now lives and how Check and Save share it, the new
  tests, and the exact suite counts before and after.
