# P12F3 — What the closing gate found: a lying health state, a flaky trial, and four majors

Amendment A3 (spec) and the brief's "no tag" clarification are already in `main`; read them first. Everything
below is the gate's own finding, not a new requirement.

## B1 (blocker) — a failed supervision write must not read as a healthy idle

`core/config_ui/__init__.py:3880–3890`: `_record_supervision` swallows the write failure. The gate's
reproduction, with real image, container and volume and no patched production code:

    authenticated Start: {"http_status": 200, "supervision_state": "not_started"}
    after launched runtime exits: 'idle: not started' exit 0
    live runtime processes: []
    failed supervision write logged: True
    runtime failure logged: the supervision record could not be written (launched)
    error: feed: could not be written

The UI accepted Start, the runtime never ran, the write failed — and the health command reports the same
`idle: not started` and the same exit 0 as an operator who simply has not pressed Start. A monitoring system
cannot tell a broken instance from an idle one.

Make publication and transition failure **fail closed**: the state must not fall back to `idle`; the health
command must report a distinct state that names the failure and return a non-zero exit; the runtime must not
be reported as running. Add a regression check that drives the producer's **real** failed-write path (the
gate did it through the feed directory's permissions or a read-only volume — say which you use, and make the
check fail if the state reads `idle`).

## B3 (blocker) — the trial is not reproducible

`test_trial_container_secrets` failed on restored-volume startup at `tests/test_container.py:36xx` in the
gate's run and passed in the operator's, on the same commit. Name the cause — a race between the restart's
health wait and the wizard's/console's state, a port still bound, a volume not yet released, an event read
before it is written — and make it deterministic. Do not widen a timeout to hide it and do not retry: the
check must fail for the same reason every time it fails. Run the five nodes **twice in a row** and paste both
summary lines.

## M1 (major) — the URL and the token are on one line

Production prints only `Configuration UI: {ui.access_url}` (`core/config_ui/__init__.py:7795`), and the URL
carries `?token=…`. Brief decision 4 and the spec require the access URL and the token on **separate,
labelled** lines, so that a log reader can copy one without the other. Print them separately, keep the token
out of the URL line in the container's log, and keep `/instance` session-free as it is.

## M2 (major) — an unallowlisted change to an existing test module

`tests/test_config_ui.py:116–130` adds an autouse token-generation filter, justified by an accidental `q7Z`
match in generated tokens. Prefer removing the dependency: a test that only passes because a random token
avoids a substring is a test that will one day fail for no reason. If a filter stays, it is a named,
enumerated allowlist entry with its reason — never an autouse hook that changes every test in the module.

## M3 (major) — the health trial does not assert AC21's deadline

`tests/test_container.py:3712` collects events through **100 seconds**, while AC21 requires the kill to be
observed within **three intervals** (3 × 30 s) plus the start period. Align the collection window with the
criterion and assert on the deadline itself, not on a longer comfortable window.

## N1, N2 (minors)

- `tests/test_container.py:50–52` still says "the Dockerfile's two stages"; the static check requires what A3
  now allows. Fix the prose.
- `docs/self-hosting.md:27–29` claims the record gives the build duration; the record does not. Either measure
  it and put it in the record, or remove the claim from both guides.

## Acceptance

The five trial nodes pass twice in a row with the documented invocation; the ordinary suite is green with no
daemon and still reports its five skips without the variable; B1's regression check fails against the old
behaviour and passes against the new one (say how you know); no new exemption outside A2's single class; one
atomic commit per concern, and say which commit carries which.
