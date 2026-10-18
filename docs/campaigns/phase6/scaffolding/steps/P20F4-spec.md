# Step P20F4 — the persistent store refuses a record written after its viewer was erased

Gate fix step of Phase 6. It answers the closing gate's **first pass**
(`docs/campaigns/phase6/full-branch-review.md`), which refused approval with 29 findings closed and five
security-boundary findings open.

## The finding (verbatim)

- **F6 OPEN** — `test_brain.py::test_ac13_forgetme_empties_the_brain_memory_of_that_key_only` and `::test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it` pass for conversation memory; baseline **2 failed**. Probe E observes **empty brain recall, persistent record present, erased text present** after the held run finishes. The old viewer text and reply are recreated on disk.

### E — erasure's late-record test passes while disk text returns (F6)

I reran `test_ac14_a_run_admitted_before_the_erasure_records_nothing_after_it` with a pytest collection hook wrapping `MemoryPlatform.close`. Immediately before the existing teardown, the wrapper read `harness.stored()` and `harness.brain.memory.recall(memory_key())`. It did not change execution or assertions.

Observed **`1 passed in 0.24s`**, plus:

```json
{"brain_recall_empty": true,
 "viewer_memory_record_exists": true,
 "erased_text_in_persistent_record": true}
```

The stored notes included:

```json
{"viewer_text":"my secret words","reply_text":"your secret reply","delivery":"confirmed"}
```

They also included the later `!forgetme` record. `_record_memory` still invokes `memory.record` after the erasure; only `ConversationMemory.remember` checks the epoch. The reviewed boundary was erasure reaching every holder and refusing records from already admitted runs. The persistent holder must reject that stale write too.

## Required resolution

Probe E: AC14 passes while the persistent record is re-created — `_record_memory` still
calls `memory.record` after the erasure, and only `ConversationMemory.remember` checks the epoch. The
disk holds the erased text again after the held run finishes.

Required: the erasure's reach includes the persistent store. A record for a `(platform, channel, viewer)`
key admitted before that key's erasure is refused by the store that owns the file, exactly as the brain's
conversation memory refuses it, and the refusal is counted/observable. A record admitted after the
erasure is written normally. The epoch the brain already maintains is the reference; the store must not
invent a second notion of time.

## Files

[`modules/viewer_memory/__init__.py`, `modules/brain/__init__.py`, `tests/test_brain.py`, `tests/test_viewer_memory.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing it.

## Tests

Probe E becomes a regression: after `!forgetme` and the held run's completion, the store holds
no record for that key and the erased text is absent from every file under the store's directory; the
refusal is observable. A record for another key, and a record admitted after the erasure for the same
key, are written. AC13/AC14 stay green, and the brain's own refusal count stays asserted.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Never the system python: it lacks aiohttp. The suite must not drop below 3,849 passed / 26 skipped.

## Authority

- The closing gate's first pass, whose findings below are quoted verbatim with their executed probes:
  `docs/campaigns/phase6/full-branch-review.md` (authoritative for this step).
- The original review: `docs/reviews/2026-10-16-full-adversarial-review.md`.
- Brief, specification, plan: `docs/campaigns/phase6/brief.md`, `spec.md`, `plan.md`.
- The phase's amendment record: `docs/campaigns/phase6/scaffolding/plan-amendments.md`.
- The pre-fix tree is `8010edc`; the phase head is the current `main`.
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon`. Working tree at `main`.

## Standing phase-6 decisions

- Phase-5 decisions stay binding: optional bridge, replacement not parallel, honest outcomes,
  default-deny, single-owner inputs, the bridge "not delivered".
- Close exactly the finding(s) this step names. If a sibling path shows the same defect, report it in
  your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. Assertions may be replaced only for a test named in
  the specification's failure allowlist, with a docstring citing its finding and requirement; elsewhere
  an assertion may only be EXTENDED (a new check on top), never reduced.
- The full suite is green at every step: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit
  (`fix(phase6): P20Fn — ...`). The scope MUST be `phase6`.
- Never weaken, skip, xfail or delete an assertion. If an existing assertion encodes the defect, replace
  it only as the specification allows and cite finding + requirement in the docstring.
- No new runtime dependency; no vendor or model name in code or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do.
- Dependencies: P20F1..P20Fn land in order; each spec assumes the previous ones are on `main`.
