# Step P14 — Connection lifecycle

Plan step `P14` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: a network boundary, a concurrency model and an ordering guarantee.
  - **One aiohttp client.** The connection is opened with one `aiohttp.ClientSession().ws_connect(url, timeout=connect deadline)`. It is the single connection (R6, R10), held by a connection-owner task spawned through `context.tasks.spawn`.
  - **Handshake:**
    1. Read the hello within `authentication_seconds`; a malformed hello → `protocol_error`.
    2. If the hello carries `authentication.{salt, challenge}` and no password is configured → `authentication_required`.
    3. Otherwise send `Authenticate` with `base64(sha256(base64(sha256(password+salt)) + challenge))` (AC24). An error answer → `authentication_failed`.
    4. Send `GetActions` within `request_seconds`, and build the inventory: name → list of entries, each with id, group and enabled. A failure → `inventory_unavailable`.
    5. Reconcile (P15).
    6. Send `Subscribe` (P16).
    7. Only then call `actions.mark_ready()` and `supervision.ready()`.
  - **Startup never fails (R1).** `prepare` registers the actions and returns. The connection attempt runs in `start_inputs`, which never raises for these causes: it reports `supervision.degraded(reason=<one of the 5>)`, which yields one `module.degraded` record (memory "coordinator-failure-names-module-only": the reason goes on `module.degraded`), and starts the reconnect loop.
  - **Losing the connection** → at once `actions.mark_not_ready()` and `degraded(reason="unreachable")`.
  - **Reconnect.** Bounded exponential backoff from `min_delay_seconds` to `max_delay_seconds`, using the context `clock` and an injectable sleeper (the pattern of `modules/clips/__init__.py:_pause_until`). Success → `ready()` again, never silently.
  - **Events while disconnected** are lost (documented in P21).
  - **Inbound reader.** One reader dispatches request responses to per-request-id futures and events to the bounded queue (P16).
  - **Shutdown.** `close()` sets stopping, fails every pending request future (a generic `ConnectionClosed` outcome that P15's run waiter later maps to `acknowledgement_lost`, D8), and closes the WebSocket within the existing close budget. `CancelledError` on the owner task propagates.
  - **Providers at this step.** The run action keeps P12's placeholder provider; it is never reached while the bridge is not ready, because the executor refuses `provider_not_ready` first. `users.read` is not registered by the bridge until P18.
  - **Credentials.** The password is a declared credential and is redacted by the loader. The module never logs the URL, the password or frame text: diagnostics carry reasons only.

## Requirements

Plan mapping: R1, R6
Acceptance criteria owned by this step: AC3, AC4, AC23, AC24, AC25, AC28
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-5 decisions that override any contrary reading of a requirement:
- **The bridge is optional in every case.** Absent, disabled or unreachable, the companion behaves
  exactly as it does today: no setting becomes required at startup, no default path changes, and no
  shipped profile depends on it. An unreachable instance produces a *ready* companion with an
  explicitly unavailable capability — never a failed startup.
- **The minimum scope is the sink and the triggers, together**: the sink runs a named action of the
  user's instance behind our own authorization, deadlines and honest terminal outcomes; the triggers
  let an event the instance routes enter the companion through the ordinary trigger policies.
- **Its events may REPLACE our basic inputs (chat, viewers) — never in parallel.** For every
  (family, platform) pair there is at most one active provider, resolved at preparation from the
  profile and the manifests, and an ambiguous configuration is refused with a diagnostic naming the
  family and both providers. Counting on the deduplication window to hide two providers is excluded.
- **Do not hide the integration's defects.** An execution the instance cannot confirm is never
  reported as a success: an acknowledged run ends `external_unknown` with its cause, and the model
  and the operator both see it.
- **This phase is NOT "delivered" at merge.** The README and the documentation index carry
  "not delivered — awaiting real-instance trial" until the trial record exists, and a test enforces it.
- **The vendor and product names never appear in `core/`.** The kernel vocabulary stays vendor-free:
  action `automation.action.run`, permission `automation.run`, destination scope `automation`,
  families `chat`/`viewers`/`cues`.
- **The approved plan's deviations D1–D8 are decisions of this phase** and override any contrary
  reading of the specification — including the two files beyond the specification's cost row:
  `core/actions.py` (`excluded_platforms`, D5) and `core/triggers.py` (`add_companion_identities`,
  D6). The D8 transmission-boundary canary must stay green; if an aiohttp upgrade breaks it, take the
  fallback route D8 documents and say which route you took in the commit message.
- The configuration UI stays a **local-only** surface; the ownership table and its diagnostics are
  displayed **read-only**, and nothing in the UI can widen the authorization set or write the
  `secrets` and `actions` blocks.

## Files

[`modules/streamerbot/__init__.py`, `tests/test_automation_bridge.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P13, P14F1]

P14F1 (a module's own health report is not overwritten by a successful phase observation, deviation
D10) must have landed first: without it, the degraded report below is overwritten by the coordinator's
`start_inputs` → `ready` observation, and an unreachable instance would leave the bridge **ready**.

All dependencies are already merged into `main` when this step runs.

## Tests

Against `FakeInstance`:
  - **AC3 (run-action half).** A loopback port where nothing listens; use a full `run` with a tmp profile enabling the bridge and a granted `automation.run` rule. The companion reaches readiness within its startup budget, the bridge has exactly 1 `module.degraded` with `unreachable`, and a run call ends `refused provider_not_ready` with 0 frames (the executor's not-ready rule, no P15 code needed). Assert the outcome rather than exit status 0 (memory "coordinator-drain-hook-tie"). The test is written as a parametrised helper so P18 adds the `users.read` half (finding P3). **Amendment (P14 blocker report):** assert also the **final state** — after the startup budget, the bridge's state is `degraded`, and no `module.ready` record was published after its `module.degraded`. AC3's literal wording would pass while an unreachable instance left the bridge ready (R6), so the record count alone is not the criterion.
  - **AC4.** The four failure modes give the four reasons, and a trace/log sweep finds the password, the URL and frame text 0 times.
  - **AC24.** The exact auth response for known salt, challenge and password; ready only after the inventory is read.
  - **AC25.** Closing the socket while ready → 1 `module.degraded` at once. After advancing the injected clock by `min_delay` → 1 `module.ready`. The recorded delays never exceed `max_delay`.
  - **AC28 (connection half).** Cancelling the owner task raises `CancelledError`. Shutdown makes the fake server observe a close within budget, and a pending `GetActions` request future is failed, not left hanging. The in-flight run clause moves to P15 (finding P3).
  - **AC23 sweep (connection scenarios).** Over this step's scenarios the configured password appears 0 times in all traces. P15 re-runs the sweep over its run scenarios.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 3,124 passed / 18 skipped, the count at the start of
this phase; the seven allowlisted tests are adapted as the specification lists them, never weakened.

## Risks

- **Hung handshake.** Each step is bounded by its own deadline, and the whole start by the coordinator's startup budget.
  - **Error answers.** The exact error frame shape for a refused authentication must be taken from the documented fixture; the trial (Q3) confirms it.
  - **Reconnect storms.** The backoff is capped and jitter-free, so tests stay deterministic.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase5): ...`, `fix(phase5): ...`, `test(phase5): ...`, `refactor(phase5): ...`, `docs(phase5): ...`).
  The scope MUST be `phase5`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase5)`.
- Keep the phase-0 to phase-4 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, the phase-3 moderation/memory/watch semantics, and the phase-4 configuration UI's
  local-only, read-only-for-secrets-and-actions surface.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
