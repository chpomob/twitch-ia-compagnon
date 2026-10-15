# Step P16 — Subscriptions, bounded inbound queue and cue mapping through the trigger engine (R6, R7a; AC26, AC27, AC29, AC30, AC33 engine half)

Plan step `P16` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: concurrency, a bounded queue, and a module-to-engine boundary.
  - **Subscribe** names only the categories that the owned pairs need:
    - `cues` owned on any platform → the custom-event category only;
    - each owned (`chat`, P) → P's chat-message and notice categories only;
    - viewers needs no subscription (pulled on demand).
  - **Bounded queue.** The reader pushes events into an `asyncio.Queue(maxsize=queue_max_events)` with `put_nowait`. On `QueueFull` it drops the event and counts it in `dropped_events`, a module counter exposed in the readiness details, and never blocks the reader, so request acknowledgements keep flowing.
  - A consumer task drains the queue.
  - **Cue envelope.** It is validated strictly:
    - `cue_id`: a non-empty string of at most 128 characters;
    - `platform`: non-empty;
    - optional `channel_id`, defaulting to `instance_identity.<platform>`; a missing default drops the cue;
    - optional `user_id` with `user_name`;
    - `text`: a string of at most 500 code points.

    A malformed or oversized envelope is dropped and counted, never truncated.
  - **Normalised event.** When the bridge owns (`cues`, platform), it builds a schema-version-2 `channel.chat.message` with `payload.kind: cue`, `message_id = cue_id`, and `metadata.source = MODULE_NAME`. The author is the envelope user, or the stable `instance:<platform>` identity. The `:` makes the identity distinct from every platform account id, since every platform module refuses `:` (see `modules/watch/__init__.py:43`), and so distinct from configured companion identities.
  - **Evaluation and publication.** The bridge evaluates the event with `context.triggers.evaluate(event, context=TriggerContext(()))` under `triggers.<module>`, then publishes through `context.bus` (guarded by P4), mirroring the twitch sequence at `modules/twitch/__init__.py:2165-2185`.
  - **Anti-echo.** At activation, `context.triggers.add_companion_identities(companion_identities.values())` (D6).

## Requirements

Plan mapping: R6, R7
Acceptance criteria owned by this step: AC26, AC27, AC29, AC30, AC33
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

[P15]

All dependencies are already merged into `main` when this step runs.

## Tests

- **AC26.** A bound of 4 and 10 events pushed before the consumer runs: 4 published, `dropped_events == 6`, and a request sent afterwards is acknowledged.
  - **AC27.** Owning only (`cues`,`*`) → the subscribe frame names only the custom category. Adding (`chat`, twitch) adds only twitch chat and notices.
  - **AC29.** One event with the exact fields. The trigger engine evaluates it with the `triggers.<bridge>` policy, and the default policy accepts it.
  - **AC30.** The same default author across 2 cues, distinct from the companion identities. 501 code points, an empty `cue_id`, or a missing `platform` → 0 events and +1 dropped each.
  - **AC33 (engine half).** With the identity set, a mapped instance chat event authored by it is rejected by the engine's anti-echo reason; this needs P17's chat mapping.

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

- **Wildcard platform.** A cue for a platform the bridge does not own is dropped by the bridge itself, before the guard counts it, and counted as dropped.
  - **Replays.** A `cue_id` reused by the instance falls in the engine's dedup window; document it.

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
