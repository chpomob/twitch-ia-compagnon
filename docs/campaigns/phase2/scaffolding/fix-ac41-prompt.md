TASK: two coupled edits in this repository (branch `plan/phase2-audio/2`), then report. Read before editing:
`docs/campaigns/phase2/spec.md` (note R3, R10, AC38–AC41), `plan.md` (note decision 1, P11, P21),
`core/actions.py` (the provider-record adoption: `_observe`, `_run_provider`), and the phase 1 tests pinning
the deadline boundary.

A verifier rejected the last revision on two points. Both are settled by ONE arbiter decision below.

## V4 — the plan bends an acceptance criterion it does not own
`AC41` currently forbids any quantity subtracted from the call deadline except R3's `capture_too_long`
admission check. The plan's transcription window (`(expiry − now) − 1 s`) is a SECOND subtraction and the plan
admits `AC41`'s letter names only the first. That is an unauthorized exception.

ARBITER RULING (apply exactly): the transcription window is legitimate and `AC41` must authorize it explicitly.
The reserve exists for one reason only — the capture's `success` record carrying its `audio_ref` must be
STAMPED BEFORE `expiry` (phase 1 adopts only in-time records), because the module must wait for the optional
transcription before it can author that record. It never shortens the capture: the recorder's kill stays at
`expiry` exactly (`AC39` unchanged), and it applies to no other action (not the audio write actions, not the
scene or poll paths).

## V1 — a delayed wake can still stamp at or after `expiry`
`core/actions.py::_observe` stamps the clock when the provider ACTUALLY finishes, so a transcription request
that wakes late could still stamp `>= expiry`, and the `success` would be discarded — losing R3's guarantee.

## EDITS

### A. `docs/campaigns/phase2/spec.md`
1. Rewrite `AC41` so the grep-able absence permits EXACTLY TWO named deadline subtractions, each with its
   purpose and its guarantee: (i) R3's `capture_too_long` admission check; (ii) the optional transcription
   window of `audio.capture`, with the ruling's wording above (stamp-before-expiry purpose, recorder's kill
   unchanged, no other action affected). Keep the `grep` assertion meaningful (name the forbidden shapes:
   adoption lead, deadline margin, early stop, deadline lead).
2. Add a new acceptance criterion (next free `AC` id): when the optional transcription does not finish inside
   its window (including a delayed provider wake), the observation is still `success` with its `audio_ref`,
   stamped STRICTLY before `expiry`, with `transcription_status` reporting the timeout — and a test drives the
   injected clock to the window edge, plus one where the transcription finishes late, asserting both.
3. Keep every other id and requirement untouched.

### B. `plan.md`
Align P11, P21 and decision 1 with the amended `AC41`:
1. Delete the `AC41`-is-only-partially-named caveat; state the second reserve as AUTHORIZED, with its purpose
   and its bound (no other action, recorder's kill at `expiry` unchanged).
2. Make the transcription window's arithmetic explicit and self-consistent, so a late wake cannot stamp
   `>= expiry`: name the quantity, state what happens when the window is already exhausted at entry (transcribe
   nothing, report it, still `success`), and state the ordering (transcription attempt → decide → author the
   record) with the stamp explicitly before `expiry`.
3. Add the two tests from A2 to the step that owns the transcription path, and keep every other step,
   decision and test intact (the five round-1 findings and round-2 V2/V3 must not regress).

## Report
- the new/edited ids and sections in both files, with the exact new `AC41` wording;
- how the late-wake case is now impossible to lose the capture;
- anything you could not encode.
Do not touch the code or any file other than `spec.md` and `plan.md`.
