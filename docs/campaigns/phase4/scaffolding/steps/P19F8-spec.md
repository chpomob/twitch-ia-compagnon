commit: fix(phase4): P19F8 — collision-free placeholders and explicit refusal of ambiguous submissions

# Step P19F8 — gate-5 finding N5 and the residual of N3

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 5 report
`docs/campaigns/phase4/scaffolding/gate-report-5.md` — read **N5** and **N3** in full, including the E/N3
and E/N5 counterexamples and the defeat table — plus the approved specification
(`docs/campaigns/phase4/spec.md`, R8 and the Check/Save criteria).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must stay
green and must not drop below **2,997 passed / 18 skipped**.

## Everything already resolved must stay resolved

Masking now happens at the data level before serialisation (N4), Check and Save share one normalisation
path and validate real values (N3's main case), and F1–F5, N1, N2 all hold: file-identity collisions
refused, no configured key in generated names, honest boolean tri-state, bounded dispatch queue, bounded
post-kill wait, structural tokens never rewritten. Do not regress any of them.

## N5 — data-level redaction collapses distinct mapping entries (data loss)

Stand-ins are allocated per **exact** secret, so two distinct **keys that merely contain** a secret are
both displayed as the same text and the entries collapse. Executed: with the secrets `q7Z` and
`second-secret`, the configured

```python
[{'action': 'x.do', 'arguments': {'xq7Z': 'one', 'xsecond-secret': 'two'}}]
```

is rendered, HTML-unescaped and JSON-decoded as

```python
[{'action': 'x.do', 'arguments': {'x[hidden]': 'two'}}]
```

— **the whole first entry, including the non-secret value `one`, disappears**. A second variant shows a
legitimate configured key that looks like a marker (`'x[hidden]'` alongside `'xq7Z'`) colliding with a
generated stand-in. Locations: `core/config_ui/__init__.py:1186`–`:1191`, `:1200`, `:1292`.

Required: **every redacted configured key gets a collision-free representation** — distinct per distinct
key, including keys redacted only by substring, and including collisions with legitimate configured text
that happens to look like a stand-in. A stand-in must never merge two entries, never displace a
non-secret value, and never be indistinguishable from another stand-in. Where a collision cannot be
avoided by construction, the render must fail loudly rather than lose data.

## N3 residual — an edited stand-in is accepted as literal text; a mixed key silently loses a value

Two executed cases remain. (a) Appending ` edited` to the displayed stand-in
`literal value configured (hidden)` is accepted as ordinary text and **written literally** on Save.
(b) Posting the displayed key plus its real key with different values is accepted by both routes and
**one value is silently lost**. Locations: `:3195`, `:4028`, `:4060`, `:1231`–`:1236`.

Required: a stand-in that has been edited (any modification other than an exact match, including added
whitespace, and any stand-in-shaped text the renderer did not produce) is **refused explicitly** with a
named reason — never written literally, never silently interpreted. A submission that carries both a
stand-in and its real value, or two entries that would map to the same key, is **refused explicitly**
rather than resolved to one of them. Ambiguity is an error, never a guess.

## Tests

- Turn the E/N5 reproduction and both E/N3 cases into running tests, asserting on the rendered document's
  decoded content (the first entry survives; the two keys are distinct) and on the Check/Save outcomes
  (explicit refusal, no literal write, no silent loss).
- Add a test that a legitimate configured value that *looks like* a stand-in is handled without
  collision, and one that a secret of a single punctuation character still renders safely.
- Keep every existing assertion green; never weaken or delete one to make this pass.

## Constraints

- Fix the production code; one atomic commit; the message is pinned at the top of this file.
- Report: the `file:line` changed, the stand-in allocation scheme you settled (in two sentences), the
  refusal outcomes, the new tests, and the exact suite counts before and after.
