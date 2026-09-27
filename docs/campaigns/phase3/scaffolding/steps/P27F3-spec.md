# Step P27F3 — gate findings F5, F6 and F7 (declaration completeness and specification consistency)
commit: fix(phase3): P27F3 — model_proposable declared on the wire, AC13 made self-consistent, core scan narrowed or cleaned

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the gate report
`docs/campaigns/phase3/scaffolding/gate-report-1.md` (read F5, F6 and F7 in full — each carries its own
evidence), the phase 3 spec (`docs/campaigns/phase3/spec.md`) and plan.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (project virtualenv).

## F5 — Minor / topology limit: the protocol declaration loses `model_proposable`

Locations: `modules/proxy/__init__.py:533`, `:568`; the analogous declaration field list in
`modules/agent_link/__init__.py`.

P1 added the optional `model_proposable` field to an action entry and the caller table claims the declaration
is carried; the proxy/agent declaration does not carry it, so a remote action cannot be marked
model-proposable. Either carry the field (and test it across the wire) or state in the spec/caller table that
the field is intentionally local-only with the reason — do NOT leave the claim and the code disagreeing.

## F6 — Minor / specification inconsistency: AC13's literal substring prohibition contradicts its hash example

Locations: `docs/campaigns/phase3/spec.md:593` (AC13 paragraph); `tests/test_viewer_memory.py:230`.

AC13 forbids a literal substring while its own example uses a hash, which the prohibition would forbid. Make
the criterion self-consistent with its example (adjust the wording, or the example) and keep the test aligned;
state the change explicitly, since a spec criterion is being clarified and not silently relaxed.

## F7 — Minor / inherited: the requested broad core-literal scan is not empty

The gate's broad core-literal scan reports hits that the phase 3 request asked to be empty. Either remove the
offending literals (preferred where they are vendor/platform names that belong in modules and the README) or
narrow the scan with a documented, falsifiable reason (which pattern is excluded, why, and what it protects).
Do not simply drop the scan.

## Constraints
- Prefer production/code corrections over wording; where the fix is a documentation or criterion clarification,
  make the change explicit and consistent on BOTH sides (spec and test).
- Never weaken an assertion; the suite must stay green and its count must not drop.
- One atomic commit; message pinned above.
- Report: `file:line` per finding and the exact wording/field change for each.
