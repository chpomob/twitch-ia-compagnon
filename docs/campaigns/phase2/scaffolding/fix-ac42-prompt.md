TASK: one honesty correction in `docs/campaigns/phase2/spec.md` (and, only if needed, the matching sentence in
`plan.md`). Do not touch anything else.

## The defect
`AC42` promises that the capture's `success` record is stamped **"never at expiry or later"** — an absolute
guarantee. `plan.md` (decision 1, P20) honestly declares the opposite limit: `core/actions.py::_observe`
assigns the stamp AFTER the provider returns, so a wake stalled by the WHOLE reserve can still land at or
after `expiry`, and the phase-1 rule then treats the record as a late success (not adopted). The criterion
and the plan contradict each other: the spec promises what the plan says cannot be promised.

## The ruling (apply exactly)
Separate what the MODULE guarantees from what is a DECLARED LIMIT:

1. **Module guarantee (testable, keep the two existing tests):** with the transcription enabled, the module
   authors and returns its observation at or before the window edge `expiry − reserve` — the injected-clock
   tests at the edge and one microsecond-class later stay exactly as they are, and the module never waits
   past the edge.
2. **Declared limit (no "never"):** the stamp belongs to the executor (`_observe`), taken after the provider
   returns, so a wake stalled by at least the whole reserve can land at or after `expiry` and the record is
   then treated as a late success (not adopted) — a residual whose cause, bound (the reserve) and mitigation
   (the reserve itself) must be stated, and which must be recorded in the README phase-2 limits clause
   (P20) beside remote-readiness and executor-cancellation. State explicitly that this is a documented limit,
   not a guarantee.
3. Reword `AC42` so a reader cannot read it as "the stamp is always before `expiry`"; keep its id, its two
   tests and its other assertions. If `plan.md` needs a one-sentence alignment (it may already be correct),
   change only that sentence.

## Report
- the exact new `AC42` wording;
- whether `plan.md` needed a change, and if so which sentence;
- confirm no other id, requirement or step was touched.
