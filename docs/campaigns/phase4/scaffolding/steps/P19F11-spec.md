commit: fix(phase4): P19F11 — one uniform identity guard that no allocator reuse can defeat

# Step P19F11 — gate-8 residual of N6/N3 (uniform refusal, allocator-proof identities)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 8 report
`docs/campaigns/phase4/scaffolding/gate-report-8.md` — read **N6** and **N3** in full, including the
defeat table and the "Reproducible N3/N6 residual" section — plus the approved specification
(`docs/campaigns/phase4/spec.md`, R4, R6, R8) and the brief.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must stay
green and must not drop below **3,046 passed / 18 skipped**.

## Context

Gate 8 confirmed N7 resolved and every published wrong-target edit refused (gate-7's remove/save case,
older renders, other sessions, other tabs, re-renders, mixed stale/current submissions). Thirteen of
fifteen items are resolved. What remains is the explicit requirement of the last pass, stated by the gate
itself: **every identity not from the render this session is currently showing must be refused, uniformly.**

## The two failing cases (both reproduced by the gate)

1. **A foreign UI process with an authentic identity** — an identity minted by another `config_ui`
   process, submitted with the receiver's current layout, fingerprint, CSRF token and current controls, is
   **accepted** instead of refused. Locations cited: `core/config_ui/__init__.py:2116`, `:2762`, `:2803`.
2. **An old identity after bounded history eviction** — a genuine identity from an earlier render becomes
   valid again once the allocator's bounded history (about 4,096 renders) evicts the entry that retired
   it, because the allocator can reissue a previously used value.

Plus a **uniformity** defect: some paths answer `200` with a failed verdict while others answer `403`, for
the same class of refusal.

## Required

- **Allocator-proof identities**: no identity value may ever be reissued. Make the identity depend on
  something monotonic and process-unique that survives history eviction (a monotonic counter combined
  with a per-process secret/nonce minted at startup, or an equivalent construction), so an evicted-then-
  recycled slot still yields a different identity. An identity from another process can never match, and
  the eviction window can never resurrect one.
- **One shared predicate, one outcome**: every route (Check, Save, Remove, and any other entry point that
  consumes an identity) decides with the **same** function and answers with the **same** status and shape
  for the same class of refusal — no `200`-with-failed-verdict in one place and `403` in another. Nothing
  is checked, applied or committed when an identity is refused.
- Keep every proven behaviour: the current render's legitimate edits still work in one submission, a
  genuinely unchanged field still sends nothing, no configured key or secret value appears in an
  identity, and the earlier guarantees (no secret rendered at any length, honest boolean tri-state, the
  layout guard shared by Check/Save/Remove, bounded queue, bounded waits, file-identity collisions) hold.

## Tests

- Both failing cases above, as running tests: a foreign-process identity submitted with the receiver's
  current guards, and an old identity made stale by exercising the allocator past its history bound
  (use the production allocator; do not weaken the bound in the test).
- A uniformity test asserting that the same refusal class yields the identical status and response shape
  on every route that consumes an identity.
- A test proving no identity value is ever reissued (mint many renders, assert all values distinct).
- Keep every earlier test green; never weaken or delete one.

## Constraints

- One atomic commit; the message is pinned at the top of this file.
- Report: the identity construction in two sentences, where the shared predicate lives, the `file:line`
  ranges changed, the new tests, and the exact suite counts before and after.
