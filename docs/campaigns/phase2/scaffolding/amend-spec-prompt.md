TASK: amend the phase 2 specification `docs/campaigns/phase2/spec.md` in this repository.

Read first: `docs/campaigns/phase2/spec.md` (the spec to amend), `core/actions.py` (the executor, especially
the provider-record adoption around `_run_provider`), and `tests/test_actions.py` (the two tests that pin the
deadline boundary). Do not modify anything except the spec file.

## The conflict the plan stage found (verified, quote it and keep it visible)

`core/actions.py::_run_provider` discards ANY provider record whose completion stamp is `>= expires_at` and
publishes its own generic record instead: `error {code: timed_out, message: "… timed out with emission …"}` —
no `result`, no `cause`, no `played_ms`. Two phase 1 tests pin that boundary:
`tests/test_actions.py::test_a_confirmation_landing_after_the_deadline_is_never_a_success` (stamp `==`
deadline → NOT adopted) and `::test_a_confirmation_landing_in_time_survives_a_late_adoption` (stamp `<`
deadline → adopted).

Consequence today: R1/AC3 ("the call deadline reached during playback → the player is terminated → the call
ends `timeout` cause `playback` carrying `played_ms`") and R3/AC10 (`error capture_timed_out`) are
UNSATISFIABLE at the literal instant: a module record produced at the deadline is stamped `>= expires_at`
and thrown away. A plan workaround (stop the player `0.1 s` before the deadline, `_ADOPTION_LEAD_SECONDS`)
was rejected: it prematurely times out a player that would legitimately finish at `expiry − 0.05`, and it
silently shortens every call's budget by a magic constant.

## RULING to encode — apply it exactly, this is the arbiter's decision

A **provider-authored interruption record** landing at or after `expires_at` is **AUTHORITATIVE** and must be
**adopted**, not discarded: outcome `timeout`, `cancelled` or `error`, produced by the module for its own
action, carrying its own `cause` (`playback`), `played_ms`, or the module's error code
(`capture_timed_out`). Reasons: the interruption already happened on the device, so the module's record is
the true one; the executor's generic record loses information the acceptance criteria require.

A provider record asserting `success`, `refused`, or a proposed action at or after the deadline is still
**discarded** and replaced by the executor's generic record — the phase 1 guarantee "a late confirmation is
never a success" is unchanged. When no provider record arrives, the executor's own timer stays in force.

## Required edits (keep every existing id stable; add, never renumber)

1. A new requirement (next free `R` id) stating the precedence above, its rationale, that the executor's
   timer remains in force when no provider record arrives, and that **no module-side "adoption lead" or any
   quantity subtracted from the call deadline may exist**.
2. New acceptance criteria covering: (a) an interrupted playback at the deadline ends `timeout` with cause
   `playback` and its `played_ms`; (b) a capture killed at the deadline ends `error capture_timed_out`;
   (c) a `success` confirmation stamped at or after the deadline is still never adopted — state that the two
   existing phase 1 tests stay green (restate their boundary explicitly); (d) grep-able absence of any
   adoption lead / shortening constant in the new modules.
3. Extend the **Target list** with the `core/actions.py` change (the adoption rule) and the tests pinning
   both sides of the new boundary.
4. Everything else stays unchanged.

## Report back (plain text, short)

- the ids you added and the exact sections/lines you changed;
- how (a)–(d) are each stated;
- anything in the ruling you could not encode, and why.
