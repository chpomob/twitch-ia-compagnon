# Phase 8 — closing gate, fifth pass

Your fourth pass refused on scope, not on substance: you were asked to measure the assertion remainder
against "the eight allowlisted functions plus A2", and the five remaining nodes belong to **Amendment A3**,
which was committed after that prompt was written. With A3 in scope your own measurement was zero.

This pass carries the correct allowance. Settle it and decide.

**Write your report to `docs/campaigns/phase8/full-branch-review-5.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else.

## The allowance, correctly stated

`docs/campaigns/phase8/spec.md` now carries:
- the **nine-entry allowlist** (eight of which have changed assertion sequences; the bilingual entry
  changes an expected set outside its assertions);
- **Amendment A2** — three nodes exempted from the isolation guard by name and journaled (installation,
  AC24 git facts, the Kick loopback listener);
- **Amendment A3** — five nodes allowlisted for a single constant each, which a phase-8 requirement
  changed: the audio input's `hasattr(start_inputs)` and `roles {"input"}`, the four appended event kinds
  in the contracts test, Twitch's `reward` kind and listable kinds, and the shipped module count 19 → 21.

## Settle F3 under that allowance

1. **Assertion preservation**: the count of changed existing assertion sequences outside the allowlist,
   A2 and A3 must be **0**. You measured 13 changed functions, 8 in the allowlist, 1 A2 node overlapping
   the eight, and 5 named by A3 — confirm that reading against the current spec text, and state the
   remainder under this allowance.
2. **A3's five**: confirm each is a constant that a named requirement changed, and that none of them
   loosened or deleted an assertion.
3. **Kick**: still byte-identical to `7782e5f`.
4. **A2's three**: real and journaled; the negative control (an unlisted node opening a socket or spawning)
   still raises — re-execute it.
5. **A1**: the AC24 comparison still excludes `tests/fixtures/**` and fails when a shipped profile is
   modified.

## Confirm the rest, briefly

- F1 and F2 remain RESOLVED (one probe each).
- Both full suites green: 3.10 (`~/.hermes/cache/scratch/venv310/bin/python`) and 3.11
  (`.venv/bin/python`), with both summary lines.
- No new skip/xfail/deletion, no new dependency, no socket outside A2's three names.

## Deliverable (`docs/campaigns/phase8/full-branch-review-5.md`)

- **VERDICT**: `APPROVE` / `REQUEST_CHANGES` / `REJECT`
- **F3**: RESOLVED / PARTIAL / UNRESOLVED — the remainder under the allowlist + A2 + A3, the A3 audit, the
  Kick diff, the A2 list, the executed negative control, the A1 evidence
- **F1, F2**: still RESOLVED
- **CI PARITY**: both summary lines
- **NEW FINDINGS** (executed reproduction only), **NOT VERIFIED**

This gate CLOSES phase 8. If everything you can check passes, say APPROVE plainly.
