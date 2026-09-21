You are the independent VERIFIER of a specification amendment. Answer with EXACTLY one JSON object,
no prose, no markdown fence.

Target: `docs/campaigns/phase2/spec.md` (the amended phase 2 spec). Compare it against the ruling below and
against the phase 1 guarantees it must not break. Also read `core/actions.py` (the provider-record adoption
in `_run_provider`) and `tests/test_actions.py` (the two tests pinning the deadline boundary).

THE RULING THE AMENDMENT HAD TO ENCODE
A provider-authored INTERRUPTION record (`timeout` / `cancelled` / `error` produced by the module for its own
action) landing at or after `expires_at` is AUTHORITATIVE and must be ADOPTED, carrying its own cause
(`playback`), `played_ms` or the module error code (`capture_timed_out`). A provider record asserting
`success`/`refused`/a proposed action at or after the deadline is STILL discarded and replaced by the
executor's generic record (phase 1 guarantee "a late confirmation is never a success", its two tests stay
green). No module-side adoption lead or any quantity subtracted from the call deadline may exist.

CHECK, each as its own result entry:
- A1: is the precedence rule stated as a NORMATIVE requirement (not a note), with the executor's timer still
  in force when no provider record arrives?
- A2: are there acceptance criteria that a reader could test for (i) interrupted playback at the deadline →
  `timeout` cause `playback` with `played_ms`, (ii) capture killed at the deadline → `error capture_timed_out`,
  (iii) a success confirmation at/after the deadline still never adopted with the two phase 1 tests named and
  kept green, (iv) grep-able absence of any adoption lead / deadline-shortening constant?
- A3: is the amendment CONSISTENT with the rest of the spec (no contradiction with R1/R3/AC3/AC10 or the
  decisions section, ids not renumbered, target list extended for the core change)?
- A4: does anything in the amendment break a phase 1 guarantee or silently weaken an existing criterion?

Output: {"results": [{"id": "A1", "status": "RESOLVED|PARTIAL|NOT_RESOLVED", "evidence": "<quote or line>", "comment": "<one line>"}, ... A1..A4], "verdict": "APPROVE|REJECT"}
APPROVE only if all four are RESOLVED.
