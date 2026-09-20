# Step P24F2 — gate findings F2, F4 and F5 (proxy bounds, normative refusal, target list)
commit: fix(phase1): P24F2 — proxy translation bound, normative attachment refusal, target list

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Same gate report
(`docs/campaigns/phase1/scaffolding/gate-report-1.md`) — these are its remaining findings.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`.

## F2 — P2 — Proxy attachment translation metadata grows without a bound — `modules/proxy/__init__.py:1470`

Every accepted upload adds an entry to `session.acknowledged`; lookups and insertions exist, clearing
happens only on session teardown. Store release and expiry never remove the mapping, and neither the
store limits nor `late_result_seconds` bound that dictionary.
Counterexample: N sequential image runs over ONE healthy connection, each released normally → store
usage returns to zero while `session.acknowledged` keeps N identifiers and reference objects.

Required fix: tie translation entries to live attachment/call ownership and prune them on completion,
release or expiry, with an explicit bound (a saturated/dropped policy that is counted, not silent).
Add a repeated-run test that exceeds the bound WITHOUT reconnecting.

## F4 — P2 — Unacknowledged image references violate the normative protocol — `modules/proxy/__init__.py:1353`

An unacknowledged reference passes unchanged to the executor and becomes `invalid_result`, whereas
`docs/proxy-protocol.md` §5.5 requires `attachment_refused`.
Executed evidence: `tests/test_proxy.py:1838`
(`test_an_image_ref_naming_an_unacknowledged_attachment_is_invalid_result_at_the_executor`) sends
`"never-sent"` and asserts `ERROR_INVALID_RESULT` — the test encodes the defect.

Required fix: reject unacknowledged references **at the proxy boundary** with the normative
`attachment_refused` code and update that test to enforce the protocol contract.

## F5 — P3 — Declared target list does not match the landed branch — `docs/campaigns/phase1/spec.md:7`

The scope comparison finds changes outside the specification's target list plus the stated exceptions:
`core/attachments.py`, `core/lifecycle.py`, `core/loader.py`, `modules/twitch/__init__.py`, several
existing test files, and the campaign scaffolding. The plan explicitly anticipates most of them, so this
is an AUTHORITY-DOCUMENT inconsistency, not a reason to revert code.

Required fix: reconcile the target list in `docs/campaigns/phase1/spec.md` with the explicitly planned
migration files, and state the permitted scaffolding scope. Do not touch `docs/design-v2.md` or the
historical root `spec.md` / `plan.md`.

## Constraints
- Fix the PRODUCTION code; never weaken, skip, xfail or delete an assertion except where the gate
  explicitly says the test encodes the defective behaviour (F4) — replace it and cite the finding.
- Scope: `modules/proxy/__init__.py`, `docs/proxy-protocol.md` (only if wording must change),
  `docs/campaigns/phase1/spec.md`, `tests/test_proxy.py`, `tests/test_hygiene.py`.
- One atomic commit; the message is pinned above.
- Report: per finding, the `file:line` changed, the test that now enforces it, anything not fixed + why.
