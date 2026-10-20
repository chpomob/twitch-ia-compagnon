# Phase 7 — closing gate, second pass

You reviewed this branch once and returned REQUEST_CHANGES with five findings
(`docs/campaigns/phase7/full-branch-review.md`). Three fix steps have landed since. Your job now is to
settle each finding against the tree, re-check the whole branch, and decide.

**Write your report to `docs/campaigns/phase7/full-branch-review-2.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no other file.

## Authorities

- `docs/campaigns/phase7/brief.md` — the operator's six binding decisions.
- `docs/campaigns/phase7/spec.md` — R1–R10, AC1–AC26, plus **Amendment A1**, which adds
  `tests/test_examples.py::test_ac30_the_chat_only_profile_starts_and_runs_the_phase_1_scenario` as a
  seventh allowlisted node (the catalog count the 19th module forces, exact constants only). A1 is part
  of the spec now; judge whether it is what it claims and whether the test still asserts exact constants.
- `docs/campaigns/phase7/plan.md`, `docs/design-v2.md`.
- Your own first report, for the findings' wording.

## Settle the five findings, each with executed evidence

**F1 (HIGH) — extraction defeated the configured deadline.** The fix had to bound extraction and the
cache write by the same deadline, with a cooperative check inside the parser, and report `timed_out`
rather than a late `success`. Re-run the original reproduction and its controls:

```python
rig = await t.read_rig(max_response_bytes=8388608, request_timeout_seconds=1, cache_ttl_seconds=0)
rig.handle._sleeper = None
rig.transport.script('example.org', t.html_page(b'<p>' * (8388608 // 3)))
observation = await rig.read('https://example.org/')   # was: 3.908 success
```
Also try the cases the loop's own review raised: a cache write after the deadline, a body that arrives as
one enormous unfinished tag (HTMLParser buffers it), and a chunk boundary inside a numeric character
reference (`b'a' * 65496 + b'&#' + b'0' * 40 + b'65;'` — was decoded as `A`). State the measured elapsed
time and the status.

**F2 (MEDIUM) — one malformed result discarded its valid sibling.** Re-run the two payloads from your
first report (one valid item plus a lone-surrogate item, Wikipedia and Brave) and the all-malformed
control. Expected: `success` with one result and one dropped, or `malformed_response` when *every* item is
unusable.

**F3 (MEDIUM) — two expectation changes exceeded the allowlist.** The AC30 node is now authorized by
Amendment A1 (verify it is a count/constant change only). The A2 node was narrowed rather than made
precise; the fix had to detect a comment naming the module (`# Enable web when the operator grants its
actions`) while still allowing `from aiohttp import web` and `web.Response(...)`. Run that
counterexample through the test's own predicate.

**F4 (MEDIUM) — three new skip paths.** `tests/test_web.py` must have no `pytest.skip` added by this
phase; the three in AC24 must be gone and the test must pass unconditionally here.

**F5 (MEDIUM) — a real socket in the guard self-test.** Re-run your audit hook over the suite (or over
`tests/test_web.py`) and state what it observes. The invariant is now: **this phase adds no socket of any
kind; the suite's only connections are the pre-existing loopback ones in `tests/test_kick.py`, which are
not a phase-7 regression.**

## Then re-check the whole branch, briefly

- The R1–R10 map still holds, and all 26 acceptance criteria are covered (you did this once; only report
  what changed).
- No new skip/xfail/deletion/loosening; only the seven allowlisted nodes have changed expectations.
- No new runtime dependency; `pyproject.toml`'s only change is the package-data line.
- Full suite green: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- The guides still state the manual trial and the revocable grant.

## Deliverable (`docs/campaigns/phase7/full-branch-review-2.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT`)
- **FINDINGS SETTLED**: F1–F5, each RESOLVED / PARTIAL / UNRESOLVED with the executed evidence and the
  measured numbers
- **REGRESSIONS**: anything the three fix steps broke
- **NEW FINDINGS**: only with a concrete counterexample or executed reproduction
- **NOT VERIFIED**: unchanged from your first pass, plus anything new

This gate CLOSES phase 7. If everything you can check passes, say APPROVE plainly.
