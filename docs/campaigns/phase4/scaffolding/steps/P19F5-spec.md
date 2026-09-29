commit: fix(phase4): P19F5 — mask every secret by value, in any position and at any depth

# Step P19F5 — gate-3 residual of F2 (HIGH): the masking is path-driven, not value-driven

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 3 report
`docs/campaigns/phase4/scaffolding/gate-report-3.md` — read **F2** in full, including the two
"defeat succeeds" paragraphs and the D/ probes, plus the approved specification
(`docs/campaigns/phase4/spec.md`, R8 and the secret-rendering criteria).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must stay
green and must not drop below **2,948 passed / 18 skipped**.

## What gate 3 confirmed fixed — do not regress it

Configured channel identifiers, rule aliases, legends and cells are redacted; hooks are positional;
the gate's six replay cases (`Ω`, `q7Z`, the long credential, `rules`, `twitch`, `combination`) show no
attribute disclosure; short and one-character secrets are hidden from ordinary inputs, error text and
triggers; `~`, `./`, `..`, mixed spellings, symlinks and hard links are refused for overlay collisions.
Keep all of that green.

## The residual (HIGH)

`_masked` masks only **declared credential paths**. A secret **value** that also appears as ordinary
nested data, or as a mapping key inside a list, is serialised verbatim. Two defeats the gate executed:

- **List key**: the collected secret `a"b` placed at `modules.brain.delivery.actions[0].arguments` as a
  mapping key — the list satisfies its schema, so it renders and the key is disclosed.
- **List value**: `modules.audio_output.voices.allowed: ['a"b']` emits `[&quot;a\&quot;b&quot;]`, and
  `json.loads(html.unescape(textarea))[0]` recovers `a"b`.

The general guarantee ("no secret VALUE appears in any page, response, log or diagnostic; only its
reference and its set/unset state") therefore still fails.

## Required

Make the masking **value-driven**: every scalar that equals a secret value — whatever its origin, at any
depth, whether it appears as a **value** or as a **mapping key**, inside a list, a nested mapping, a
textarea, an attribute, a label, a JSON field, an error or a diagnostic — is withheld and replaced by the
same stable placeholder the rest of the UI uses. A secret value must not be recoverable after
HTML-unescaping and re-parsing the rendered document. Keep the legitimate visibility intact: a
non-secret value the operator configured stays visible, and a secret's *reference* and its set/unset
state remain visible.

Prefer computing the set of secret values once (from the declared credential settings, resolved through
the same environment the runtime uses) and applying one recursive replacement over the rendered data,
rather than enumerating allowed paths. Where a value cannot be resolved (an unset reference), say so
rather than guessing. Convert both defeats into running tests, including the HTML-unescape round-trip.

## Constraints

- Fix the production code; never weaken an existing assertion. If a test encodes the residual behaviour,
  invert it and cite the finding in its docstring.
- One atomic commit; the message is pinned at the top of this file.
- Report: the `file:line` changed, how the value set is computed and applied, the new tests, and the
  exact suite counts before and after.
