# Step P14 — `modules/moderation`

Plan step `P14` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Manifest v2.** `moderation.request`: version 1, write,
    `model_proposable: true`, permission `moderation.request`, destinations
    `*/*/moderation`, no delivery, idempotency `none`.
    - Arguments: `operation` (enum `delete_message`, `timeout`),
      `message_id`, `reason` ≤ 200, `duration_seconds` (integer, only valid
      with `timeout`, required with it).
    - The module consumes `channel.chat.message`. No grant.
  - **Settings and validator.**
    - `mode` (`alert` default, `propose`, `act`) and
      `channels.<platform>/<channel_id>.mode` overrides;
    - `act.operations` (default `[delete_message]`);
    - `max_timeout_seconds` (1–3600, 300);
    - `max_actions_per_window` (1–20, 3), `window_seconds` (60–86400, 600);
    - `per_target_cooldown_seconds` (0–86400, 600);
    - the `propose.*` settings are declared here and used in P15.
  - **Prepare.** Resolves `moderation` services per platform and reads the
    chat context and the companion identity.
  - **Message index.** Built from consumed `kind == message` events
    (decision 7).
  - **Modes:**
    - `alert` → `success disposition alerted`, 0 platform requests;
    - `act` → apply now.
  - **Application.** The strict rules are checked in the spec's order, each
    refusing with its code and 0 requests. `duration_out_of_range` is
    checked after the platform service's rounding hook. An allowed
    application:
    1. `mark_emitted`;
    2. one service request;
    3. the result maps to `ok` → `success applied`, `rejected` →
       `error platform_rejected`, `rate` → `error rate_limited_platform`,
       lost/5xx/deadline → `external_unknown`;
    4. the window and target-cooldown counters count every sent request
       (see "Concurrency" below: they are reserved before the send).
  - **Concurrency (serialization and reservation).** Several runs (and, in
    P15, approval commands and `auto_apply`) can call the application
    concurrently, and the service request is an `await`. Without
    protection, two calls could both pass the `rate_limited` and
    per-target cooldown checks while the first request is pending. The
    module therefore holds one `asyncio.Lock` per `(platform, channel_id)`
    for the **check-and-reserve** section only:
    1. under the lock, evaluate every strict rule against the current
       counters;
    2. still under the lock, if allowed, reserve the slot: append the
       window timestamp and set the target-author cooldown instant;
    3. release the lock, then `mark_emitted` and send the one request.
    A reserved slot is always followed by a send (nothing between the
    reservation and the send can refuse), so "counters count every sent
    request" still holds and a reservation is never rolled back, whatever
    the platform result (the conservative choice: a lost or rejected
    request still consumed its slot). The request itself runs outside the
    lock, so a slow platform does not block unrelated channels or the
    checks of other targets beyond the reservation. P15's approval and
    `auto_apply` paths call this same function; there is no second
    application path.
  - **Facts.** Every request publishes exactly one `moderation.decision`
    fact (mode, disposition/status, code, operation, platform, channel,
    message id, target author id, reason ≤ 200). The audit module records
    it through its existing fact subscription. Verify that its pattern
    covers the new type, and document otherwise.
  - **Packaging.** Add the pyproject line and the running catalog counts.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC24, AC25, AC27, AC28
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

[modules/moderation/__init__.py, modules/moderation/module.yaml, pyproject.toml, tests/test_moderation.py, tests/test_examples.py, tests/test_profiles.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3, P6, P8]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC24: the default mode alerts, with 0 service requests and 1 fact.
  - AC25 on the real module: with a scripted model, `moderation.request` is
    offered only with a rule, and a second request in a run is
    `refused run_limit`.
  - AC27 on twitch/fake: each rule's code with 0 requests:
    - the moderator, VIP, broadcaster and companion cases for
      `target_protected`;
    - the 4th application inside 600 s for `rate_limited`.
  - AC28: an allowed delete sends 1 request and ends `applied`; the
    rejected, rate and lost cases each send 0 second requests.
  - Concurrency: with a scripted service whose request blocks on an
    `asyncio.Event`, `max_actions_per_window: 3` and 5 distinct targets,
    5 `moderation.request` calls launched with `asyncio.gather` give
    exactly 3 service requests and 2 `rate_limited` refusals (checked
    before the event is set). Two concurrent calls on messages of the
    same author give 1 request and 1 per-target cooldown refusal. Two
    concurrent calls on different channels both send (the lock is per
    channel).
  - The fact count equals the request count over every case.
  - No test uses a permanent-ban path: a grep of the module for
    ban-without-duration is empty.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- `target_protected` depends on roles seen in the indexed event. A
    message older than the index but still in the chat context is
    `target_unknown`, which is the conservative choice. The README states
    this.
  - The per-target cooldown key must be the target **author**, not the
    message.
  - Concurrent applications could overshoot `max_actions_per_window` or
    hit one target twice inside its cooldown if the counters were updated
    after the awaited request. Mitigation: the per-channel lock and
    reservation above. Risk of the mitigation: a lock held across an
    `await` would serialize platform latency; the lock covers only the
    synchronous check-and-reserve, never the request.

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
