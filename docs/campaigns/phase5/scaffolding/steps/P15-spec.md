# Step P15 — Allowlist, reconciliation and the run-action provider with the full outcome table (R4, R5; AC16–AC21, AC45)

Plan step `P15` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: branching outcomes and an "at most one frame" invariant.
  - **Reconciliation** runs at each successful connection:
    - The reachable set is the configured `action:` names, plus every instance action of a configured `group:`, that exist exactly once and are enabled.
    - It emits exactly one value-free diagnostic trace, `automation.allowlist.reconciled`, that lists unmatched entries by configured name.
  - **Run provider.** Decisions happen strictly before any frame, in this order:
    1. Not in the allowlist → `refused not_allowlisted`. An empty or absent allowlist refuses everything.
    2. Absent from the inventory → `refused instance_action_missing`.
    3. A name with 2+ instance actions → `refused instance_action_ambiguous`.
    4. Disabled → `refused instance_action_disabled`.
    5. An argument violation → `error invalid_arguments`.
       - The deciding schema is the action's `action:` entry alone if one names it.
       - Otherwise the action must satisfy **every** matching `group:` entry's schema.
       - An entry without a schema accepts only `{}`.
       - Validation uses `validate_against_schema`.
  - **Not emitted until the boundary (D8).** The provider's first statement, before any `await`, is `invocation.mark_not_emitted()`.
  - **Sending:**
    - Serialise one `DoAction` frame naming the **instance action id** from the inventory. This never relies on the instance's handling of a nonexistent name (R8, question 1).
    - Then the transmission segment of D8, with no `await` before the transport write: the pre-write check (bridge `connected` flag, `ws.closed`, protocol transport present and not `is_closing()`). If it fails → return `refused provider_not_ready`, 0 frames, emission stays `not_emitted`. Otherwise `invocation.mark_emitted()`, set the per-call `sent` latch, and `await ws.send_str(frame)`. The write happens before that coroutine's first suspension (D8). Any exception or `CancelledError` from that await is post-write → `external_unknown`, cause `acknowledgement_lost`.
    - Before the segment, the provider awaits an injectable `_before_transmit` hook (default: returns at once). It is a test seam that makes the pre-send interval observable, and it sits strictly before `mark_emitted()`.
    - Await the response future up to `acknowledgement_seconds`.
    - An acknowledgement → `external_unknown`, code `external_effect_unknown`, cause `execution_unconfirmed`.
    - An error answer → `error provider_failed`, cause `instance_rejected`.
    - No acknowledgement before the deadline, or the connection lost after sending → `external_unknown`, cause `acknowledgement_lost`.
    - `CancelledError` after sending → the provider-authored interruption record with cause `acknowledgement_lost`, per D8 and the clips precedent.
    - Every non-success has `retryable: false`, and the uncertain messages state that "the companion does not know whether the action ran".
  - **Invariants.** No retry, never `success`, and at most one frame per call (a per-call `sent` latch).
  - **Not-ready** is left to the executor (`provider_not_ready`). A pre-send deadline or cancellation is left to the executor's emission rule, which yields `timeout`/`cancelled` because the provider marked `not_emitted` and the segment has not run.
  - **In-flight runs at shutdown.** The run waiter maps P14's failed request future (`ConnectionClosed`) to `external_unknown`, cause `acknowledgement_lost`.

## Requirements

Plan mapping: R4, R5
Acceptance criteria owned by this step: AC16, AC21, AC45
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

[P14, P15F1]

P15F1 (the executor carries a provider's cause when it takes over an emitted call, deviation D11) must
have landed first: without it, a cancellation coming from the **executor** (its caller, or its own
deadline) drops the provider's cause, and this step's `cause: acknowledgement_lost` assertions on those
paths cannot hold. The paths where the provider answers — its own acknowledgement deadline, a late
acknowledgement, a connection loss after sending, an in-flight run at shutdown, a send that raises
after the mark — are unaffected either way.

All dependencies are already merged into `main` when this step runs.

## Tests

Against `FakeInstance`, one test per R4 row (**AC16**, 10 tests). Each asserts status, code and cause exactly, `retryable: false`, and the fake server's request-frame count: 0 for the first 6 rows and for "before the frame left", 1 otherwise. A suite-level assertion: no run observation has `status: success`.
  - **AC17.** A late acknowledgement gives `acknowledgement_lost`, `executor.provider_invocations` unchanged after the result, and exactly 1 frame for the `call_id`.
  - **AC18.** A run of `B` against `allowlist: [{action: "A"}]` → `not_allowlisted` with 0 frames. An empty allowlist refuses everything. With a group entry, `A` is reachable and `B` is refused.
  - **AC19.** The argument schema cases.
  - **AC45.** The action entry beats the group entry, so `A {n:1}` sends 1 frame. Two group entries must both accept: `invalid_arguments` with 0 frames, and an argument set both schemas accept sends 1 frame.
  - **AC20.** Exactly 1 reconciliation diagnostic naming `C`, `D` and `E` and not `A`; runs of `C`/`D`/`E` give `ambiguous`/`disabled`/`missing` with 0 frames, and `A` sends 1 frame.
  - **AC21.** A principal with no `actions:` rule → `refused not_authorized`, with 0 provider invocations.
  - **Transmission boundary (finding P6).** With `_before_transmit` blocked on an event:
    - cancelling the call → `cancelled`, existing code, 0 frames at the fake server;
    - letting the executor's deadline expire → `timeout`, existing code, 0 frames;
    - closing the fake connection, then releasing the hook → `refused provider_not_ready`, 0 frames.
    - Cancelling **after** the segment (acknowledgement withheld) → `external_unknown`, cause `acknowledgement_lost`, exactly 1 frame.
    - **Pre-write failure (finding P6, round 2).** With `_before_transmit` blocked, make the client's transport closing without a loop turn reaching the bridge (the test closes the protocol transport, or patches its `is_closing` to return `True`), then release the hook → `refused provider_not_ready`, `invocation.emission == not_emitted`, 0 frames at the fake server, and the bridge reports not-ready.
    - **Post-write interruption.** Pause the client protocol's writing (`protocol.pause_writing()` with the writer's output size pushed over its limit), so the send suspends in `_drain_helper` after the write. Cancelling the call there → `external_unknown`, cause `acknowledgement_lost`, emission `emitted`, exactly 1 frame once the protocol resumes. Expiring the executor's deadline there gives the same outcome.
    - A static check of the provider's source (AST) asserts no `await` between the pre-write check, the `mark_emitted()` call and the `ws.send_str` call, and that `mark_emitted()` comes after the pre-write check.
    - **aiohttp canary (D8).** The `ast` test of the installed `WebSocketWriter.send_frame`: no `Await` before `_write_websocket_frame` in the uncompressed branch, and only the two closing-check raises before the write. Plus a test that the bridge's `ws_connect` call passes `compress=0`.
  - **AC28 (in-flight half, moved from P14).** A run whose frame was sent and whose acknowledgement is withheld, then bridge shutdown → `external_unknown`, cause `acknowledgement_lost`, 1 frame.
  - **AC23 sweep (run scenarios).** The configured password appears 0 times in all traces.

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

- **Stale inventory.** An action renamed after connection → the instance's error answer → `instance_rejected`. The guide documents the reconnection-time reconciliation.
  - The executor's own timeout must stay larger than the acknowledgement deadline (P12 validator), or the executor's generic record pre-empts the cause.
  - **Client internals.** D8's "no await before the transport write" was read in the installed aiohttp 3.14.3. The canary test fails loudly if an upgrade changes it (`pyproject.toml` allows `>=3.9,<4`). The fallback is the module-local synchronous writer of D8. Record the route taken in the commit message.
  - **Pre-write check drift.** If aiohttp adds a new pre-write raise, the canary's "only the closing checks" assertion fails before the boundary can silently misreport.

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
