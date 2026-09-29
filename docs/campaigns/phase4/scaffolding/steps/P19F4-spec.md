commit: fix(phase4): P19F4 — redaction never rewrites structural tokens; the boolean tri-state submits real booleans

# Step P19F4 — gate-2 finding N1 and the remainder of F3

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 2 report
`docs/campaigns/phase4/scaffolding/gate-report-2.md` — read **N1** and **F3** in full, including their
executed reproductions, plus the approved specification (`docs/campaigns/phase4/spec.md`, R4/R8 and the
secret-rendering criteria).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must stay
green and must not drop below **2,917 passed / 18 skipped**.

## N1 — MEDIUM — short-secret redaction corrupts boolean option values

The new boolean select runs its **semantic option tokens** through the secret text renderer, so a
configured short secret whose text occurs inside `true` or `false` rewrites the token: the gate's
`test_gate2_boolean_redaction` renders

```html
<option value="t[hidden]ue">t[hidden]ue</option>
```

and submitting exactly that rendered option sends `{'probe': 't[hidden]ue'}` — a **string rather than a
boolean** — to Check and to the real Save route. Locations: `core/config_ui/__init__.py:1746`, `:2655`,
`:3206`.

Required: redaction applies to **rendered data** (the values an operator configured, wherever they would
otherwise appear), and **never** to the page's own structural tokens, option keys, attribute names or
serialised form values that the UI itself generates. The boolean control must render and submit real
booleans (`true`/`false` as values that the parser maps to `True`/`False`), and Check and Save must
receive an actual boolean. Keep the guard from P19F1/P19F3 intact: a genuine secret value must still
never be rendered — the fix must not reopen F2. Turn the gate's reproduction into a running test,
including the case where a configured short secret's text occurs inside `true` and inside `false`.

## F3 — the honest-boolean guarantee is incomplete

Gate 2 confirmed the original case is fixed (an unset `probe` with `default: true` renders its effective
value, and a draft expressing `false` reaches the runtime settings parser as `False`). The remaining gap
is the one N1 describes: a legitimate boolean option cannot always be submitted truthfully. Close it by
fixing N1, and keep the three states distinct end to end — unset (inherit, no overlay entry), explicit
`false`, explicit `true` — with a test that a `false` over a `default: true` survives rendering,
submission, parsing and the runtime's settings parser.

## Constraints

- Fix the production code; never weaken an existing assertion. If a test encodes the defective
  behaviour, invert it and cite the finding in its docstring.
- One atomic commit; the message is pinned at the top of this file.
- Report: for each item, the `file:line` changed, how you separated rendered data from structural tokens,
  the new tests, and the exact suite counts before and after.
