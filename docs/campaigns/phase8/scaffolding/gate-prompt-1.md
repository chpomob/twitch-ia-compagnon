# Phase 8 — full-branch closing gate

You are the closing reviewer of phase 8. The phase makes trigger types something a module declares, so
that channel points, an operator's button, a spoken wake word and a timed random trigger stop being kernel
changes. Sixteen steps have landed; decide whether the tree deserves approval by checking the claims, not
by reading them.

**Write your report to `docs/campaigns/phase8/full-branch-review.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else.

## Authorities

- `docs/campaigns/phase8/brief.md` — the operator's seven binding decisions.
- `docs/campaigns/phase8/spec.md` — R1–R9, AC1–AC40, the acceptance-criteria introduction (append-only
  numbering), and the test-failure allowlist.
- `docs/campaigns/phase8/plan.md` — the sixteen steps and the gate.
- `docs/design-v2.md`, and the pre-existing code the phase changes: `core/triggers.py`,
  `core/contracts.py`, `core/loader.py`.

## First job: the requirements and the criteria

For each of **R1–R9**, name the running tests that carry it and state PASS/FAIL/NOT-VERIFIABLE. A criterion
is PASS only if its assertion exists and fails when the behaviour is removed. Cover all **40 acceptance
criteria**, including the later ones whose numbering was appended (they belong to R2, R3, R6 and R7).

## Second job: try to defeat the invariants, with executed evidence

- **Narrow-only.** Configure a declared type that asserts a field its input does not emit, or a value
  outside the declared schema: it must be **rejected at configuration time**, never accepted and then
  never firing. Show the rejection and its reason.
- **Unknown types stay refused.** A type no module declares, and a type a module declares with a bad
  schema, are still refused.
- **The hook cannot pass by accident.** A hook that raises, and a hook that exceeds its bound, both decide
  **no trigger** — including inside a `none_of` policy, where a naive combination would turn the failure
  into an acceptance. Execute that composition.
- **A trigger grants nothing.** A triggered event still goes through admission and budgets, and an
  ungranted action is still refused: drive one event end-to-end and show the refusal.
- **The vocabulary is append-only.** `reward`, `manual`, `speech`, `timer` are appended; every pre-existing
  kind keeps its index and meaning; a route that names an old kind behaves as before.
- **Channel points.** A fake redemption is normalized to `reward`, matched by a `reward` type keyed on the
  reward identifier, and a redemption for another reward does **not** trigger.
- **The operator's surfaces.** A press from each of the page, the hotkey command and the overlay emits the
  **same** event with the configured button identifier; a page press without the token is refused; the
  press command has a real entry point and its exit codes are honest.
- **Audio privacy and cost.** With a detector that never fires: **zero** transcription calls, and no
  recording kept. With a declined candidate: nothing acquired, nothing transcribed. With an accepted one:
  the segment is acquired with the configured duration and pre-roll (measure the observed length and
  start offset, not just the fact), transcribed once, and the run receives the `speech` event. A dead
  detector reports `module.degraded` once, recovers visibly, and rearms.
- **The timed random.** An `interval` policy fires within its window on the injected clock, respects its
  probability, and does not fire outside its active window.

## Third job: the invariants of the suite

- No added `skip`, `xfail`, `importorskip` or deleted test; only the allowlisted expectations changed, and
  each change is a count or constant the new inputs and kinds force. Compare assertion ASTs.
- No new runtime dependency (`aiohttp`, `PyYAML` only).
- **No test opens a socket**: the surfaces, the detector, the Twitch client and the audio device are
  faked; the only sockets in the suite remain the pre-existing loopback ones in `tests/test_kick.py`,
  which this phase does not touch.
- Full suite green: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.

## Fourth job: what an operator actually gets

- The shipped example profiles behave exactly as before for a profile that enables none of this.
- The guide (English normative, French translation) explains how to declare a trigger type, how to
  configure each of the four new families, how to run the detector (a command or an HTTP endpoint), and
  the manual real-instance trial — including a hand-written record file that a pytest node cannot produce.
- Editing a trigger policy in the configuration UI is possible where the UI already exposes policies.

## Deliverable (write to `docs/campaigns/phase8/full-branch-review.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT`)
- **REQUIREMENT MAP**: R1–R9 with their named tests and PASS/FAIL; the 40 criteria accounted for
- **INVARIANT ATTEMPTS**: one row per attack, with the observed result and the numbers
- **TEST TREATMENT**: allowlist audit, socket check, suite count
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly — no real Twitch redemption, no real microphone, no real wake word

This gate CLOSES phase 8. If everything you can check passes, say APPROVE plainly.
