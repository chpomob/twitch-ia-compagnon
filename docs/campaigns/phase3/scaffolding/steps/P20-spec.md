# Step P20 — `modules/youtube`

Plan step `P20` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Manifest v2.** Role `input`.
    - Trigger types as for kick.
    - Credentials `client_secret` and `refresh_token`, plus a non-secret
      `client_id` setting.
    - Settings: `channels`, `companion_name`, `min_poll_interval_seconds`
      (1–60, 5), `quota.daily_units`, `quota.write_reserve_units`,
      `quota.costs` (`list`, `insert`, `delete`, `ban`, `broadcast_lookup`),
      `notices.kinds`. Plus the validator.
  - **Token manager.** The refresh token is exchanged at the token
    endpoint. The access token is refreshed at `expires_at − refresh_margin`
    (a fixed 60 s constant, documented), and only when a request needs it.
    A refused refresh publishes one `module.degraded` naming
    `auth_refresh_failed`, and polling stops.
  - **Poller.** Resolves the channel's active broadcast `liveChatId`, then
    lists at `max(pollingIntervalMillis/1000, min_poll_interval_seconds)` on
    the injected clock.
  - **Quota ledger.**
    - Every request is charged its configured cost.
    - A read is issued only if `remaining − cost ≥ write_reserve_units`.
      Otherwise the module reports `quota_exhausted` (degraded, once per
      day) and stops reading.
    - A send is issued only if `remaining ≥ cost`. Otherwise it is
      `refused quota_exhausted` with 0 requests.
    - The ledger resets at 00:00 America/Los_Angeles (decision 10), and
      reads resume.
  - **`conftest.py`** gains `ScriptedTokenEndpoint` (issued tokens,
    refusals, a request log) and `ScriptedLiveChatAPI` (broadcast lookup,
    list pages with `pollingIntervalMillis`, insert/delete/ban answers, a
    per-endpoint request count).
  - **Packaging.** Add the pyproject line and the running catalog counts.

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC36, AC37
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.
Phase-1 product decision that overrides any contrary reading: delivery is a **configured, pluggable
terminal step** — a configured ordered list of delivery actions (mode `fixed`, or mode `modules`
derived from the enabled modules that declare a delivery capability, with a configurable preference
order), each entry declaring how the answer text maps into its arguments (a named argument, or none
for an effect-only delivery such as a stream-scene change); every entry runs at the terminal step
only, each receiving the text only if declared; the model never selects the delivery and never
performs an intermediate effect; adding a delivery module must require no change to the agentic loop.

## Files

[modules/youtube/__init__.py, modules/youtube/module.yaml, pyproject.toml, tests/test_youtube.py, tests/conftest.py, tests/test_examples.py, tests/test_profiles.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4, P7]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC36:
    - a 3600 s token is refreshed once before expiry and not earlier;
    - a refused refresh gives 1 `module.degraded auth_refresh_failed`;
    - a 2000 ms server interval with min 5 gives ≤ 12 lists per 60 s;
    - an 8000 ms interval gives 8 s spacing.
  - AC37: 100 units, list cost 5, send cost 50, reserve 50 → exactly 10
    lists, then `quota_exhausted` and 0 reads. The send check is exercised
    in P21 once `chat.write` exists. After the LA midnight, reads resume.
    The LA-midnight function is tested at both DST transitions and on an
    ordinary day.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- The AC37 arithmetic: 10 lists × 5 = 50 leaves 50 = reserve, so the
    11th read would dip into the reserve and must not issue. The boundary is
    `remaining − cost ≥ reserve`.
  - The broadcast lookup is also charged. Mitigation: the AC37 test sets a
    lookup cost of 0 or accounts for it. The test states which it uses.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase3): ...`, `fix(phase3): ...`, `test(phase3): ...`, `refactor(phase3): ...`).
- Keep the phase-0 guarantees in force: bounded admission, phase lifecycle, explicit terminal action
  outcomes, default-deny authorization (reads included), bounded retention, a single global
  startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss diagnostics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- No new runtime dependency beyond the existing ones unless this step is the one that adds it; no
  model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
