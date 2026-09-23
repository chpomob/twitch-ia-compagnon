# Step P22F1 — gate findings F1, F2, F3 (readiness, outcome and reasons of the audio actions)
commit: fix(phase2): P22F1 — unavailable actions refuse provider_not_ready; empty endpoint never ready; distinct reasons

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the gate report
`docs/campaigns/phase2/scaffolding/gate-report-1.md` (read it first — it carries the executed counterexamples
X1..X4), the phase 2 spec `docs/campaigns/phase2/spec.md` (R2, R8, R10, AC7, AC29, AC43) and the plan
`docs/campaigns/phase2/plan.md`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv (the system python lacks `aiohttp`).

## F1 — Major: unavailable individual actions produce `error no_provider`, and tests accept it

Locations: `modules/audio_output/__init__.py:930`, `modules/audio_input/__init__.py:1164`,
`modules/stream_control/__init__.py:891`, `core/actions.py:1122`; assertions in `tests/…`.

R2/AC7 and R8/AC29 require a call to an **unavailable action** to be **`refused provider_not_ready` with ZERO
provider invocations**. Today a call answers `error no_provider`, and the passing failed-probe delivery test
DOCUMENTS the deviation (it asserts `AUDIO_SPEAK: ("error", ERROR_NO_PROVIDER)`); the output/capture tests
accept it too. Fix the production behaviour and update those assertions to the normative outcome — that is a
legitimate contract change, not a weakened test. The gate's `X1` block is the counterexample to turn green.

## F2 — Major: disabling the probe makes an empty speech endpoint READY

Locations: `modules/audio_output/__init__.py:907` and `:918`; `tests/test_audio_output.py:821`.

AC43 (and the arbiter's no-provider decision) require `audio.speak` to stay **unbound** until an operator
configures an endpoint. With `synthesis.endpoint: ""` and `synthesis.probe: false`, the action is currently
considered ready. An EMPTY endpoint is never ready regardless of the probe setting; the probe decides the
readiness of a CONFIGURED endpoint only.

## F3 — Moderate: empty and unreachable endpoints have the same not-ready reason

Location: `modules/audio_output/__init__.py:945`; coverage gap at `tests/test_profiles.py:396` and `:427`.

AC43 expressly requires **distinct value-free reasons** for the empty and the unreachable cases. `X1`
(prepare both with usable outputs and `probe: true`) shows both emit exactly the same reason string. Give each
case its own value-free reason and assert both, through the real prepare path.

## Constraints
- Fix the PRODUCTION code; never weaken, skip, xfail or delete an unrelated assertion. Where an existing
  assertion encodes the defect, change it to the normative outcome and say so in the commit body.
- Scope: the three phase 2 modules, `core/` only if the `no_provider` mapping itself is the defect, and the
  tests that assert these behaviours.
- Turn the gate's `X1` counterexample into a RUNNING test (it is written to assert the current defect: invert
  its expectations to the normative ones and keep it executable).
- One atomic commit; the message is pinned above.
- Report: `file:line` changed per finding, the test that now enforces each, anything not fixed and why.
