commit: fix(phase4): P19F7 — mask data before serialization and restore before validation, one path for Check and Save

# Step P19F7 — gate-4 findings N3 and N4 (the two faces of the placeholder design)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 4 report
`docs/campaigns/phase4/scaffolding/gate-report-4.md` — read **N3** and **N4** in full, including their
executed reproductions — plus the approved specification (`docs/campaigns/phase4/spec.md`, R8 and the
Check/Save criteria) and the brief (decision 3a).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must stay
green and must not drop below **2,971 passed / 18 skipped**.

## Context: what is already correct, and must stay correct

Gate 4 confirmed every earlier finding resolved: the file-identity collision guard (F1), the
no-configured-key-in-generated-names fix (F2's disclosure half — the value-driven masking), the boolean
tri-state (F3), the bounded dispatch queue and post-kill wait (F4, F5), the non-corruption of structural
tokens (N1), and Check honouring the layout digest (N2). Do not regress any of them: a genuine secret
value must still never be rendered, at any length, in any position or nesting depth.

## The two defects

**N4 (MEDIUM)** — the redaction is applied as a **textual substitution over already-serialised output**,
so JSON's own punctuation and escape characters are treated as secret data. A one-character secret `"`
turns the entirely legitimate `voices.allowed = ['kept', 'voice-two']` into
`[[hidden]kept[hidden], [hidden]voice-two[hidden]]`, which is invalid JSON: the control becomes
uneditable and Save refuses (403). A one-character backslash reproduces it through a legitimate value
containing a quote (`ordinary[hidden]"quote"`). Locations cited by the gate:
`core/config_ui/__init__.py:1394`–`:1398`, `:1433`–`:1444`, `:1950`.

**N3 (MEDIUM)** — Check applies the parsed edits directly, while Save calls protection/restoration
first, so the two paths validate **different** things. With `voices.allowed = ['kept', 'a"b']`,
`voices.default = '${GATE_SECRET}'` and `GATE_SECRET = 'a"b'`: the page shows the second item as a
placeholder, Check returns 200 with a **false failure** (`field 'voices.default': must be an allowed
voice`), and Save of the identical submission returns 303 after passing `['kept', 'a"b', 'added']` — a
draft the real checker passes. Locations: `:3179`, `:3282`, `:3995`, `:4010`.

## Required correction (the gate's own wording, made precise)

1. **Mask data, not text.** Produce the rendered document by masking actual **scalar and key content**
   *before* serialisation, then serialise and HTML-escape normally with no redaction of delimiters,
   quotes, brackets or escape sequences. No substitution may run over serialised text. A secret of any
   length — including `"`, `\`, `[`, `]`, `{`, `}`, `,`, `:` — must never make a legitimate control
   unreadable or uneditable, and must never be recoverable from the rendered document.
2. **One draft path for Check and Save.** Both routes must normalise a submitted draft identically
   before validating: the same placeholder restoration and the same normalisation, covering nested keys,
   reordered list elements and ambiguous or repeated placeholders. Check must validate the **real**
   values, so a valid configuration can never produce a false failure and an invalid one can never pass.
   Factor the shared step into one function used by both routes rather than duplicating it.
3. Where a placeholder cannot be restored unambiguously (the text was edited, duplicated or reordered),
   refuse the submission with an explicit outcome naming why, rather than guessing at a value.

## Tests

- Turn both reproductions into running tests: the one-character `"` and `\` cases proving legitimate
  lists stay valid JSON, editable, and Saveable; and the `voices.default`/`voices.allowed` case proving
  Check's verdict equals the real checker's verdict for the same submission.
- Add a test that a secret value of every length in the gate's published set (`Ω`, `q7Z`, the long
  credential) is still never rendered and never recoverable — the earlier guarantees must hold under the
  new mechanism.
- Keep every existing assertion green; never weaken or delete one to make this pass.

## Constraints

- Fix the production code; one atomic commit; the message is pinned at the top of this file.
- Report: the `file:line` changed, the masking/restoration shape you settled (in two sentences), the new
  tests, and the exact suite counts before and after.
