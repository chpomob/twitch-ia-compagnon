# Step P17 — Chat mapping for owned platforms and role trust (R7b; AC32, AC33, AC43)

Plan step `P17` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: a per-platform mapping that must equal the native shape.
  - **Mapping.** For each owned (`chat`, P), map P's documented chat-message event and each documented notice kind to a normalised event. The payload key set and `kind` must equal what P's native module produces: `platform`, `channel_id`, `author{id, display_name[, roles, roles_provenance]}`, `message_id`, `text`, and `kind` only for notices, per `modules/twitch/__init__.py:401-430`. Kick and youtube are checked the same way.
  - **Unmapped kinds.** An instance event with no documented mapping → dropped and counted.
  - **Roles** are emitted only when `trust_instance_roles` is true, with `roles_provenance` naming the bridge's module. Otherwise no `roles` key is emitted and `TriggerContext(())` is used, so a moderator audience rule is not satisfied.
  - **Anti-echo.** The events go through the same evaluate → publish path as P16, so the engine's anti-echo applies.
  - The mapping table is a module-local dict keyed by (platform, documented event name). Platform names appear only in `modules/streamerbot`.

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC32, AC33, AC43
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

[P16]

All dependencies are already merged into `main` when this step runs.

## Tests

- **AC43.** For each P in {twitch, kick, youtube}, owning (`chat`, P): the chat fixture and every documented notice fixture each give exactly 1 event whose kind and key set equal the native module's for the equivalent input. Native shapes are built inline in the test by calling each native module's own normaliser on its existing test payloads (e.g. the twitch notification `event()` at `modules/twitch/__init__.py:401`), not by stored fixtures, so P13's provenance guard stays about instance frames. An undocumented notice gives 0 events and +1 dropped. Owning only twitch, the kick and youtube fixtures give 0 events.
  - **AC32.** Key and kind equality for twitch. With `trust_instance_roles` false, an `audience: moderators` rule is unmet by the fixture's moderator flag; with `true`, it is met.
  - **AC33.** `--check-config` on a profile where the bridge owns (`chat`, twitch) with no identity fails naming `twitch`. With the identity set, an event authored by it is rejected by the engine.

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

Native payload shapes can drift. The test derives them from the native code, not from a copy. A notice whose required fields the instance does not document is not mapped, by rule.

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
