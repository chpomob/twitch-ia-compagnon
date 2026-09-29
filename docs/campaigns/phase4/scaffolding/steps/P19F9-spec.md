commit: fix(phase4): P19F9 — form edits are patches over stable identities, never a reconstruction of masked text

# Step P19F9 — architectural fix for the placeholder class (gate-6 N3, N5, N6)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 6 report
`docs/campaigns/phase4/scaffolding/gate-report-6.md` — read **N3**, **N5** and **N6** in full, including
every executed counterexample — plus the approved specification (`docs/campaigns/phase4/spec.md`, R4, R6,
R8 and the Check/Save criteria) and the brief (decisions 3a, 5a, 6c).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must stay
green and must not drop below **3,018 passed / 18 skipped**.

## Why this step is architectural, not another patch

The last four findings (N3, N5, N6 and N4 before them) all come from one design choice: the page renders a
**masked copy of the configuration** and the server tries to **reconstruct the real values** from the
masked text the browser posts back. Masking is lossy, so distinct values collapse into one stand-in (N5),
an edit can retarget another entry (N6), an edited stand-in is indistinguishable from literal text (N3),
and punctuation cannot be told apart from data (N4). Patching this scheme keeps discovering new corners.

The operator has decided the replacement: **the form transports edits, not a document.** The server owns
the truth; the browser sends only what changed, identified stably, and the server merges those changes
onto the real configuration it loaded.

## Required design

1. **Stable, opaque field identities.** Every editable control carries an identity allocated at render
   time that is unique per (configuration path, mapping key, list position), revealed to the browser only
   as an opaque token, and mapped back server-side to the exact place it came from. No configured **key**
   text and no secret value appears in that identity or anywhere in the page (F2 and the whole masking
   requirement stay satisfied).
2. **Patch submission.** A submission carries, per control, either "unchanged" or the new value, plus the
   field's identity — never the whole document, never a masked rendering of a value the operator did not
   touch. The server applies the patch to the real configuration and validates the **resulting** real
   document, so Check and Save compute their verdict on identical real inputs (closes the N3 divergence).
3. **No reconstruction.** No route may infer a real value from masked text. A field left untouched keeps
   its real value without ever being sent; a submission that cannot be applied unambiguously (an unknown
   or stale identity, a token from another page, two entries claiming one identity, an edit inside a
   portion the page withheld) is **refused explicitly** with a named reason and changes nothing — never
   resolved by guessing.
4. **Structured values.** A list or mapping the operator edits is presented and submitted in a way that
   preserves per-entry identity: prefer per-entry controls over one blob. Where a raw-text control remains
   (for convenience), it must be sent only when it was actually edited, carry its own identity, and be
   **refused** when the text it contains cannot be applied without reconstructing a withheld portion. A
   legitimate edit must never silently change a different entry (N6), never lose a non-secret value (N5),
   and never be rejected for containing punctuation or a string that merely looks like a marker.
5. Keep everything the earlier rounds established: no secret value rendered, recoverable or echoed at any
   length; no configured key in a generated attribute; the honest boolean tri-state; the layout digest
   shared by Check, Save and Remove; the bounded dispatch queue; every wait bounded; the file-identity
   collision guard.

## Tests

Cover, at minimum:
- Gate 6's N6 reproduction: two list entries whose withheld keys differ only by which secret they contain,
  with distinct visible values; change **only** one visible value and assert the other entry's key and
  value are untouched, on both Check and Save.
- Gate 6's N5 reproduction: the two-keys-containing-secrets case, asserting no entry disappears and both
  keys survive on the way back.
- Gate 6's N3 cases: an unmatched or truncated identity is refused explicitly (Check refuses identically
  to Save, no literal write, nothing committed).
- An unknown/stale/duplicated identity is refused, and a legitimate edit that merely contains punctuation
  or marker-looking text still applies.
- The earlier guarantees: a secret of every published length is never rendered and never recoverable; the
  boolean tri-state round-trips; Check's verdict equals the real checker's.

## Constraints

- One atomic commit; the message is pinned at the top of this file.
- Never weaken an existing assertion; if a test encodes the superseded mechanism, replace it and cite this
  step in its docstring.
- Report: the identity scheme in two sentences, the patch shape the routes accept, the `file:line` ranges
  changed, the new tests, and the exact suite counts before and after.
