commit: fix(phase4): P19F10 — per-render field identities and an explicit unchanged/set/clear operation

# Step P19F10 — the last residuals of the identity class (gate-7 N3, N6) and N7

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 7 report
`docs/campaigns/phase4/scaffolding/gate-report-7.md` — read **N6**, **N3** and **N7** in full, including
their defeat tables — plus the approved specification (`docs/campaigns/phase4/spec.md`, R4, R6, R8) and
the brief.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must stay
green and must not drop below **3,035 passed / 18 skipped**.

## Context: the redesign works; these are its last corners

Gate 7 confirmed N5 resolved, the patch-over-stable-identities mechanism holding against a dozen defeat
attempts (identical hidden keys, identical visible values, reordering, removal, duplication, foreign-page
tokens, cross-process tokens), and F1–F5, N1, N2, N4 still resolved. Everything below is inside that
mechanism, not a new design. Keep every one of those passes green.

## N6 / N3 residual — identities can be reused across renders, so a stale token retargets

The gate's failing case: after a first channel is removed and a second is saved with the same policy, an
**old token equals the second entry's new token**, so a submission carrying the stale token is accepted
and applied to the wrong place. Related to it, N3 remains open on the same seam (an ambiguous or stale
identity must be refused, never resumed).

Required: field identities are **unique per render**, so an identity produced by an earlier render can
never coincide with a current one. A submission carrying an identity that is not from the render the
session is currently showing must be **refused explicitly** with a named reason (the same "reload the
page" outcome the layout guard already produces), changing nothing and committing nothing — never
applied to a different place, never silently accepted. The refusal must be identical on Check and Save.
Cover the gate's exact case (remove one entry, save another with the same policy, then submit the old
token) and keep the identity opaque: no configured key and no secret value may appear in it.

## N7 — an empty string cannot be expressed

A scalar whose value is withheld renders as an empty replacement flagged as withheld, and the control
treats "the value equals the rendered replacement" as "unchanged", so the operator cannot set a field to
the **empty string**: Check reports passed with no edit in the draft, Save reports nothing to save, and
the previous value stays. Entering two quote characters stores a literal `\"\"` instead — not a
workaround. Both real validations accept the true empty-string draft, so the UI is the only thing in the
way.

Required: transport **"unchanged" independently of the replacement value** — an explicit per-field
operation (`unchanged`, `set`, `clear`/remove) rather than inferring it from equality with the rendered
text. An operator must be able to set an ordinary scalar to the empty string, and to clear an optional
entry, with Check and Save agreeing on the real resulting document. A genuinely unchanged field must
still send nothing.

## Tests

- The gate's stale-token case, asserting explicit refusal on both routes and zero commits.
- An identity from an older render of the same page, and one from a different session, both refused.
- Setting a withheld ordinary scalar to the empty string: Check's draft contains the edit, Save commits
  it, and the stored value is the empty string.
- Clearing an optional entry, and a control that removes a list entry.
- The previously proven defeats stay refused, and the earlier guarantees (no secret rendered at any
  length, no configured key in a generated name, honest boolean tri-state, shared layout guard) stay
  green.

## Constraints

- One atomic commit; the message is pinned at the top of this file. Never weaken an existing assertion.
- Report: the identity scheme and the operation flag in two sentences each, the `file:line` ranges
  changed, the new tests, and the exact suite counts before and after.
