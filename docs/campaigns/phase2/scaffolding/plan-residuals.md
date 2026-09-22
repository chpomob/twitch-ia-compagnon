# Phase 2 — plan stage: closing record and residuals

The plan stage ran five review rounds (plan #1: 5 findings; plan #2 with those findings as input: 3 new
findings; then three focused fix rounds driven by the arbiter). **The plan is closed by the arbiter with the
residuals below documented, per the campaign rule "a reviewer at its limit does not block closure — residuals
are recorded".** The substance of every finding is settled; what remains are wording-precision objections about
the exact phrasing of one timing guarantee.

## Settled by the arbiter (and now normative in the spec)

- **R10 + AC38–AC41** — a provider-authored **interruption** record (`timeout`/`cancelled`/`error`) landing at
  or after `expires_at` is **authoritative and adopted** (it carries the module's `cause`, `played_ms` or error
  code); a provider record asserting `success`/`refused` at or after the deadline is still discarded. The
  executor's timer stays in force when no provider record arrives. This removed the previous workaround (a
  magic 0.1 s lead that silently shortened every call budget).
- **AC41 (v1.2)** — exactly **two** named deadline subtractions are authorized, each with purpose and bound:
  R3's `capture_too_long` admission check, and the optional transcription window of `audio.capture`, whose only
  purpose is that the capture's `success` record carrying its `audio_ref` is stamped before `expiry`. The
  recorder's kill stays at `expiry` exactly; no other action is affected.
- **AC42 (v1.2)** — the capture's transcription path separates a **module guarantee** (it never waits on
  anything scheduled at or after the window edge `expiry − reserve`, and returns at or before that edge) from a
  **declared limit** (the stamp belongs to `core/actions.py::_observe`, so a stall of the whole reserve can land
  at or after `expiry` and the record is then treated as a late success).

## Residual objections carried forward (verifier round 5, wording precision)

1. `AC42`'s "never waits on anything scheduled at or after the edge" versus waiting for a **bound scheduled
   exactly at** the edge — a reader can still see a tension at the boundary instant.
2. A remaining absolute phrasing in the plan's P11 for the same instant.
3. The declared limit's **bound is stated as the reserve alone**, while the real delay is (wake delay +
   execution/preemption before the executor's stamp); the bound should be read as "the reserve is the mitigation,
   not a proof".

## Arbiter's ruling on these residuals

They are **phrasing**, not contract, defects: the guarantee and the limit are both stated, and the numbers are
the implementation's to pin. Deciding the exact wording of "at the edge" without a running clock is precisely
the kind of precision that belongs in the implementation step, where the test pins the instant and the
full-branch gate reviews the code. The three objections are therefore **handed to the implementation**:

- `P11` (the step that owns the transcription window) must pin the edge instant with an injected clock test and
  state the module's behaviour in one sentence with no absolute phrasing;
- `P20` must record the stalled-wake limit with its real bound (reserve as mitigation, not proof) beside the
  other documented limits;
- the phase-2 gate must check both.

If the implementation cannot satisfy the module guarantee as written, that is a **finding for the gate**, not a
blocker for the plan.
