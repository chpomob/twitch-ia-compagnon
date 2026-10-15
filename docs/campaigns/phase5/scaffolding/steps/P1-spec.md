# Step P1 — Kernel vocabulary

Plan step `P1` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: it is a public data-format change to the closed `EVENT_KINDS` vocabulary. Additive changes to `core/contracts.py`:
  - **`cue` kind.** `EVENT_KINDS` gets `"cue"` appended as the 10th member, after `watch_tick`, so the existing indices keep their order. `EVENT_KIND_SET` follows from it.
  - **`PLATFORM_NOTICE_KINDS`** becomes `tuple(k for k in EVENT_KINDS if k not in ("message", "watch_tick", "cue"))`. Its 7 members are unchanged.
  - **Family vocabulary.**
    - `INPUT_FAMILIES = ("chat", "viewers", "cues")` and `INPUT_FAMILY_SET`.
    - `INPUT_OWNER_NONE = "none"`.
    - `CHAT_FAMILY_KINDS = ("message", *PLATFORM_NOTICE_KINDS)` (D1).
    - `CUE_KIND = "cue"`.
    - `input_family_of_event(event) -> tuple[str, str] | None`. It returns `(family, platform)` for a `channel.chat.message` whose `payload.kind` (default `message`) is in `CHAT_FAMILY_KINDS` (family `chat`) or is `cue` (family `cues`). It returns `None` otherwise: `watch_tick`, other event types, or a malformed payload. The platform is read from `payload.platform`.
  - **Gated actions.** `INPUT_FAMILY_GATED_ACTIONS = MappingProxyType({"viewers": "users.read"})`. `users.read` is an action name already present in `core` (no vendor and no platform), so R10 and AC11 hold.
  - **Counter and trace.** `COUNTER_INPUT_OWNERSHIP_REJECTIONS = "input_ownership_rejections"` is added to `DIAGNOSTIC_COUNTERS`. Add `TRACE_INPUT_OWNERSHIP_REJECTED = "input.ownership.rejected"`.
  - **Declaration contract.** Add a frozen `InputFamilyDeclaration(family, platforms: tuple[str, ...], default: bool)` and `parse_input_families(value, field) -> tuple[InputFamilyDeclaration, ...]`. The parser refuses:
    - a non-list value;
    - an entry with keys other than exactly `family`/`platforms`/`default`;
    - a family outside the vocabulary;
    - an empty platform list, a non-text platform, or a `*` mixed with concrete names;
    - a duplicated (family, platform) within one manifest;
    - a non-bool `default`.

    Each refusal is a `ContractError` naming the field path.
  - Export every new name in `__all__`.

  Adapt `tests/test_contracts.py` for the two allowlisted tests only:
  - `test_event_kind_vocabulary_is_the_nine_names_in_order` now asserts the 10 names, `cue` last.
  - `test_platform_notice_kinds_exclude_message_and_watch_tick` additionally asserts that `cue` is absent and the 7 members are unchanged.

## Requirements

Plan mapping: R2, R3, R10
Acceptance criteria owned by this step: (see plan)
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

[`core/contracts.py`, `tests/test_contracts.py`, `tests/test_input_ownership.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_input_ownership.py` (new), contract unit tests:
  - `parse_input_families` accepts the 4 shipped shapes, including `['*']`, and refuses each malformed case with the field named.
  - `input_family_of_event` maps `message` and each notice kind to `chat`, `cue` to `cues`, and `watch_tick` to `None`.
  - `"cue" not in PLATFORM_NOTICE_KINDS`.
  - A fresh `Counters()` exposes `input_ownership_rejections == 0`.
  - The vendor/module-name grep of `core/contracts.py` returns 0 (AC11, partial).

  Run the full suite. Expect only the 2 allowlisted contract tests to need the adaptation above. Record any other failure: per the spec's blind spot, for example a test asserting the exact `DIAGNOSTIC_COUNTERS` tuple. Extend the allowlist only with a test whose assertion R3 contradicts, and name it in the P22 report.

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

- Consumers that iterate `EVENT_KINDS` will see `cue` automatically: the `core/triggers.py` `event_kind` enum, the brain's route validation, and the module-local mappings in kick and youtube (which use their own names). P11 covers the brain.
  - A consumer that treats an unknown `kind` like a `message` could act on cues: `viewer_memory`, `moderation`, `watch`, `chat_context`. P11 checks each of them.

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
