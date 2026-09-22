TASK: amend the phase 2 implementation plan `plan.md` (working tree, branch `plan/phase2-audio/2`) so that it
resolves exactly three findings from the challenger. Read `plan.md` fully first, plus
`docs/campaigns/phase2/spec.md` (the authority, amended: note R3, R10, AC38-AC41) and the code the findings
cite. Change ONLY what these three findings require; keep every other decision, step id, ordering, test and
rationale intact (the plan already resolves five earlier findings — do not regress them).

## Finding P1 — blocker — step P11
"The transcription deadline path cannot preserve the required successful capture."
Evidence: R3 requires transcription failure to leave the observation `success` with its `audio_ref`. P11 bounds
transcription by `min(timeout_seconds, expiry − now)` and promises success for every transcription failure, but
its cancellation handler "ends cancelled". When the remaining call budget expires first, either cancellation
produces `cancelled`, or the transcription timeout produces `success` stamped at or after `expiry`, which P3
explicitly discards. No step or test resolves this boundary while preserving R3 and R10.
Required: state the boundary explicitly and make it testable — which outcome wins when the call budget expires
during transcription, how R3's "capture stays success with its audio_ref" is preserved (the audio is already
captured and stored), and what R10 (provider interruption records are authoritative) means for that path.

## Finding P2 — major — step P13
"The publication guard does not preserve compatibility with contexts lacking a service registry."
Evidence: P4 says `for_module builds ModuleServices(self.services, name)` even when `services is None`, and that
its publish raises `RuntimeContextError` when the registry is absent. P13 nevertheless publishes "when the
context carries a services facade", assuming "a context without one publishes nothing". A default
`RuntimeContext` now carries that facade, so this guard attempts publication and fails activation. This
contradicts the caller table's claim that existing direct `RuntimeContext` constructions need no change.
Required: guard the UNDERLYING REGISTRY's availability (not the facade's presence), keep the caller-table claim
true, and add a test that activation through a default context publishes nothing and does not fail.

## Finding P3 — major — step P4
"The accepted service interface omits enumeration required by the poll consumer."
Evidence: P4 says `_require_surface` must not demand entries (a two-method fake registry stays valid), accepting
collaborators with only publish and resolve. P16 unconditionally calls `context.services.entries()` to discover
platforms. The plan specifies an empty enumeration only for an absent registry, not for a present two-method
collaborator.
Required: define ONE consistent interface (either enumeration is part of the accepted surface and the fake
registry implements it, or the consumer must not call it) and test the accepted collaborator THROUGH
`stream_control.prepare`, so the two steps cannot disagree again.

## Constraints
- Update the plan's own decision/findings sections so the three resolutions are visible (a reader must not have
  to diff steps to see them).
- No new step ids unless a resolution genuinely needs one; if it does, append at the end and say why.
- Do not touch the spec, the code, or any file other than `plan.md`.
- Report back: the step(s) and sections changed, the exact wording of each resolution, and anything you could
  not resolve.
