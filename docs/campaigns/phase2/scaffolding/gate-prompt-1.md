# Phase 2 — full-branch review gate (closing gate before delivery)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`, full diff of the phase 2 campaign against
the phase 1 delivery point.

**Write your report to `docs/campaigns/phase2/scaffolding/gate-report-1.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no other
file.

## Authority

- `docs/campaigns/phase2/spec.md` (v1.2+, R1–R10, AC1–AC43) — the spec under test
- `docs/campaigns/phase2/plan.md` (P1–P21) — what was supposed to be built
- `docs/campaigns/phase2/scaffolding/plan-residuals.md` — the residuals the arbiter closed the plan stage with
- `docs/design-v2.md` §6 Phase 2 and §4.4 (the seven starter units)
- `docs/proxy-protocol.md` — protocol v1, normative
- `docs/campaigns/phase1/` — phase 0/1 guarantees that must NOT regress

## Your job

1. **Map every requirement to a NAMED RUNNING TEST.** For each of R1–R10, name the test that enforces it and
   the acceptance criteria it covers. Run the suite yourself (`.venv/bin/python -m pytest tests/ -q
   -p no:cacheprovider`). A requirement with no named test is a finding.
2. **Adversarially attack the phase 2 guarantees**, from the code and from RUNNING tests:
   - **R10 precedence**: a provider-authored interruption record (`timeout`/`cancelled`/`error`) landing at or
     after `expires_at` is adopted, carrying its cause/`played_ms`/error code; a `success`/`refused` record at
     or after the deadline is still discarded and the executor's timer stays in force when no record arrives.
     Try to break BOTH sides, including the interaction with the phase 1 tests that pin the boundary.
   - **The two authorized deadline subtractions** (AC41): R3's `capture_too_long` admission check and the
     `audio.capture` transcription window. Confirm no THIRD subtraction exists anywhere, that the recorder's
     kill stays at `expiry`, and that the audio write / scene / poll paths have no such window.
   - **AC42's guarantee vs its declared limit**: the module never waits on anything scheduled at or after the
     window edge; the stalled-wake residual is truly documented where `P20` promised it.
   - **AC43 / no default provider**: the shipped profiles and examples carry an empty speech and transcription
     endpoint, `audio.speak` is unbound with a value-free named reason, zero requests are attempted, and a
     chat-only or freshly installed profile starts clean. Try to find any code path, example, profile or test
     that assumes a localhost or vendor default.
   - **One command / one effect**: never two poll creations; a lost confirmation resolves by
     reconciliation or `external_unknown` (`confirmation_lost`), never a false confirmation; scene read-back
     and serialization per shared resource.
   - **Bounds**: poll tracking table, acknowledgement translation table, audio segment duration/bytes, TTL,
     attachment cleanup, the runtime service registry. Look for unbounded growth across sequential runs.
   - **Media over protocol v1**: audio references travel by reference with a bounded transfer; no local path is
     assumed usable remotely; `audio_ref` lease/expiry/cleanup mirrors `image_ref`.
   - **Honesty**: no weakened/skipped/xfailed assertion, no swallowed error, no stub validator, no test bent to
     match an implementation. Note that AC41/AC42 legitimately changed some expectations — distinguish a
     legitimate contract change from a weakened assertion.
   - **Platform neutrality**: no OBS/Twitch/vendor/device literal in `core/` or in the brain; the vendor name
     lives in the module and the README only.
   - **Degradation**: with OBS absent, `stream_control` is discovered-but-not-ready with a value-free reason,
     and a chat-only profile passes.
3. **Check the residuals** in `plan-residuals.md` were honoured by the implementation (the P11 edge test, the
   P20 limit record) or record them as findings if not.
4. Report the per-provider integration trial records in `docs/README.md` honestly (what ran, what did not, and
   why), and state plainly anything you could not verify.

## Deliverable (write to `docs/campaigns/phase2/scaffolding/gate-report-1.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **REQUIREMENT MAP**: R1–R10 → named running test + AC coverage
- **CHECKS**: one line each, PASS/FAIL/NOT-VERIFIABLE with the command used
- **FINDINGS**: only with a concrete counterexample or an executed reproduction, each with `file:line`
- **NOT VERIFIED**: stated plainly

This gate closes phase 2. If everything you can check passes, say APPROVE plainly.
