# Step P8 — `twitch` manifest and ingestion gate (R2, R3; AC6, AC12)

Plan step `P8` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: it changes branching in the ingestion start.
  - **Manifest.** Add `input_families: [{family: chat, platforms: [twitch], default: true}]` with a comment.
  - **Gate.** In `modules/twitch/__init__.py`, `start_inputs` (line 1148) opens the EventSub WebSocket and every chat, notice and follow subscription only when `context.inputs.owns("chat", "twitch")`.
  - **When not owned:**
    - no socket and 0 subscriptions;
    - one value-free diagnostic, `twitch: chat ingestion owned elsewhere`;
    - the module still declares and binds `chat.write`, keeps its `handle_chat_send` subscription, and marks itself ready in `prepare` (line 1094), as today.
  - The ownership check reads only the context view: a context built without ownership owns nothing. Existing tests build the context without an ownership table, so the module must treat an **absent table** as "own everything I declare". `OwnedInputs.unrestricted()` covers this: it is the default when `for_module` gets no `ownership`.
  - **`tests/test_examples.py`** (allowlisted): add `input_families` to the expected key set for `twitch`. P9, P10 and P12 extend the same tables.

## Requirements

Plan mapping: R2, R3
Acceptance criteria owned by this step: AC6, AC12
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

[`modules/twitch/module.yaml`, `modules/twitch/__init__.py`, `tests/test_twitch.py`, `tests/test_examples.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_twitch.py`, using the existing fake EventSub/Helix harness:
  - **AC12.** With ownership (`chat`, twitch) → bridge stand-in, `start_inputs` opens 0 EventSub subscriptions (the fake server records 0 connections), while `chat.write` is bound, ready, and a granted `chat.write` reaches the fake Helix send endpoint once.
  - **AC5 (no fallback).** With the bridge stand-in owning the pair, the twitch notification handler is driven directly and the published event is dropped by the guard (counter +1).
  - **AC6.** `twitch` plus a bridge stand-in with no `inputs`: `twitch` owns (`chat`, twitch) by default.
  - Run `tests/test_twitch.py` in full with no ownership table, to confirm unchanged behaviour.

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

- **Default when no table.** P4 must make "no ownership installed" mean unrestricted, or every existing twitch test breaks.
  - **Follow subscription.** It belongs to the notice kinds, so it is gated too.
  - **`chat.write` readiness.** It must not depend on the EventSub socket.

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
