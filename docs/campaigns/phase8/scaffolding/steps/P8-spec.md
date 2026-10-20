# Step P8 — Channel points on Twitch (R5; AC17, AC18, AC19, AC20; allowlisted twitch type list)

Plan step `P8` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- Manifest: `notices.kinds` enum and the generic `event_kind` enum (`modules/twitch/module.yaml:172`)
    gain `reward`; `triggers.fields` declares `reward.id` (string), `reward.title` (string),
    `reward.cost` (integer); `triggers.types` gains the declared type `reward` (kinds `[reward]`,
    schema: required `reward_ids` array of strings, optional `min_cost`/`max_cost` integers ≥ 0,
    rule `{all: [{in: [{field: reward.id}, {param: reward_ids}]}, {gte: [{field: reward.cost},
    {param: min_cost}]}, {lte: [{field: reward.cost}, {param: max_cost}]}]}` — absent bounds are true
    per R2). Default policy unchanged.
  - Code: `_NOTICE_KINDS`/validator (`modules/twitch/__init__.py:275`, `_validate_notices` `:395`)
    accept `reward`; activation adds one subscription `channel.channel_points_custom_reward_redemption.add`
    version `1`, condition `{broadcaster_user_id: <configured broadcaster>}` iff `reward` is listed;
    a refused/unanswered one is one value-free `module.degraded` naming capability `reward`, chat
    continues (same shape as the follow refusal, `:84`). A notification is normalized into a
    schema-2 `reward` event (`message_id` = redemption id, author = user id/display name, text =
    trimmed `user_input` or the reward title, `payload.reward = {id, title, cost}`); missing
    redemption id / reward id / integer cost (bool is not an integer), or a user id containing `:`,
    publishes nothing and increments `counts["invalid"]`. Decision and admission go through the
    existing twitch ingestion path (`:2426`); its trace uses `decision_trace_payload`.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC17, AC18, AC19, AC20
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-8 decisions that override any contrary reading of a requirement:
- **A module declares its trigger types; the kernel evaluates them.** The four generic types
  (`probability`, `audience`, `keyword`, `event_kind`) stay and remain usable by any input. A declared
  type carries a declarative match rule; no module code runs in the hot path for it.
- **A declared type narrows, never widens, and grants nothing.** Configuration is validated against the
  module's declared schema *and* the kernel's canonical one. A type cannot assert a field its input
  does not emit, and a trigger only decides candidacy: admission, budgets and DEFAULT-DENY are
  untouched and still decide everything after it.
- **The one hook is bounded and fail-closed.** It runs only on events its own module emitted, under a
  hard timeout; raising or exceeding the bound means *no trigger*, never a silent pass, and its outcome
  is recorded like every other decision.
- **Event kinds are appended, never reordered.** `reward` and `manual` join the closed vocabulary at the
  end, the way `cue` did; earlier indices keep their order and meaning.
- **No new runtime dependency.** `aiohttp` and `PyYAML` remain the only ones. The wake-word detector is
  configured (a command or an HTTP endpoint), exactly like an audio source or the transcription
  endpoint; a detector is never embedded and never assumed.
- **Streamer.bot stays optional.** Its `cue` path is unchanged, and nothing in this phase needs it.
- **No continuous listening.** Audio publishes an event only when a trigger or a run asked for it; a
  run that nobody requested must not record audio, and continuous transcription is out of scope.
- **No test opens a socket.** Twitch, the detector, the surfaces and the audio device are faked; the
  real-instance trial stays a documented manual procedure with its own record.
- Close exactly the requirement(s) this step names. If a sibling path shows the same defect, report
  it in your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. The full suite is green at every step:
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (never the system python: no aiohttp).

## Files

[`modules/twitch/module.yaml`, `modules/twitch/__init__.py`, `tests/test_twitch.py`, `tests/test_examples.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P7]

All dependencies are already merged into `main` when this step runs.

## Tests

AC17 (unset → 0 reward subscriptions; `[reward]` → exactly 1 with type, version,
  condition); AC18 (refused → exactly 1 `module.degraded` naming `reward`, chat still published, no
  token/credential in payload); AC19 (`"  hello  "` → `hello`; empty → `Hydrate`; same id twice → 1
  decision; 3 malformed + `a:b` → 0 events, `counts["invalid"]` +4); AC20 (the four
  `reward{r1,100,1000}` rows; `reward{[r1]}` at cost 0 and 100 000). Allowlisted
  `test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` twitch type list
  becomes 5 names. Existing `test_twitch.py` tests pass unmodified; P1's AC16 comparison passes (no
  shipped profile lists `reward`).

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

Adding a subscription unconditionally would change every profile's subscriptions
  (AC16) — gated strictly on `reward` in `notices.kinds`. A redemption must not enter the chat-notice
  path (it is not a platform notice, A1). Fulfil/refund is out of scope; no Helix write is added.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase8): ...`, `fix(phase8): ...`, `test(phase8): ...`, `refactor(phase8): ...`, `docs(phase8): ...`).
  The scope MUST be `phase8`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase8)`.
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
