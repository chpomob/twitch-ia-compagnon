# Step P24F1 — gate findings F1 and F3 (attachment ownership across run lifetime)
commit: fix(phase1): P24F1 — attachment ownership on rejected observations and late proxy uploads

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Phase-1 work is already committed; the
full-branch gate (`docs/campaigns/phase1/scaffolding/gate-report-1.md`) returned REQUEST_CHANGES with
these two PRIORITY-1 findings. This step fixes exactly those two, nothing else.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`.

## F1 — P1 — Rejection deletes another live run's attachment — `core/actions.py:1622`

The executor correctly rejects a foreign lease, then **unconditionally discards every attachment named
by the rejected observation**.

Executed evidence: `tests/test_observations.py:400`
(`test_an_image_leased_to_another_run_is_invalid_result_through_the_context`) creates an attachment for
`run-2`, returns it from `run-1`, and asserts the whole store becomes empty. It passes today.
Consequence: if `run-2` still needs that image for its next model turn, `run-1`'s invalid observation
destroys it and the unrelated run fails.

Required fix: pass the rejecting call's `run_id` into cleanup and discard **only attachments owned by
that run**. Change the foreign-lease tests to assert the owner's bytes REMAIN readable (and that the
rejecting run's own attachments are still cleaned).

## F3 — P1 — Late uploads can recreate leases after cleanup or charge another run

`modules/proxy/__init__.py:1413`, `:1423`, `:1456`. A pending header retains `run_id` but not its call
identity; binary-frame processing stores bytes without checking whether that call is still live; and an
explicitly supplied but unknown/terminal `call_id` falls back to the latest unrelated call.

Concrete counterexamples to cover:
1. Accept a header for run A; cancel A and publish its terminal record, releasing its store usage; then
   receive the binary frame → today `_on_binary` creates a NEW attachment for the already-cleaned A with
   no future run cleanup.
2. A terminates while B remains in flight; a delayed header explicitly naming A's call is assigned to B
   by `_run_for_attachment`.

Required fix: retain the owning call identity in the pending header, reject an explicit unknown/terminal
call (never fall back to an unrelated call), revalidate ownership **immediately before storing** the
binary payload, and invalidate pending headers when their calls terminate. Add both interleaving tests.

## Constraints
- Fix the PRODUCTION code; never weaken, skip, xfail or delete an assertion except where the gate
  explicitly says the test encodes the defective behaviour (F1's foreign-lease assertions, F3's fallback
  expectation) — in that case REPLACE the assertion and cite the gate finding in the docstring.
- Scope: `core/actions.py`, `modules/proxy/__init__.py`, `docs/proxy-protocol.md` (only if the normative
  text must state the call-identity rule), `tests/test_observations.py`, `tests/test_proxy.py`,
  `tests/test_actions.py`, `tests/test_retention.py`.
- One atomic commit; the message is pinned above.
- Report: per finding, the `file:line` changed, the test that now enforces it, anything not fixed + why.
