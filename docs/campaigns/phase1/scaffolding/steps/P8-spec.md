# Step P8 — Fixture modules `fakeplatform` and `fakeeffects` (R5, R1; AC31, AC50–AC54, AC57 support)

Plan step `P8` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`fakeplatform` (manifest v2, role `input`, `produces: [channel.chat.message]`, `consumes: [channel.chat.send]`, credentials none, settings `channel_ids[]`, `companion_name`, optional `feed: {messages: [{channel_id, author, message_id, text}], after_event: <type> | null}`, default keyword trigger `${companion_name}`, `actions: [chat.write]` mirroring twitch with `platform: fake`, `delivery: {text_argument: text}`): `prepare` binds a `chat.write` provider (records sends, scriptable outcomes `success`, `FAIL_BEFORE_EMISSION`, `FAIL_AFTER_EMISSION` through a module-level `TRANSPORTS` registry keyed by channel and an optional `transport` settings seam), appends to `context.chat`, evaluates the trigger engine, publishes schema-version-2 events for platform `fake` (with `author.roles`/`author.roles_provenance` when the fed message carries them); `inject(event)` on the handle drives a message in-process; `start_inputs` publishes the scripted `feed` immediately or on every occurrence of `after_event` (decision 6). `fakeeffects` (manifest v2, roles `[]`, no grant): provides `audio.say` (write, `delivery: {text_argument: text}`, args `{text}`, destinations `*/*/audio`), `stream.set_scene` (write, `delivery: {text_argument: none}`, args `{scene}`, destinations `*/*/stream`), `overlay.raw` (write, no `delivery`, args `{payload}`, destinations `*/*/overlay`); every provider records invocations (`calls[]` with arguments) and can be scripted to raise or to return `refused`/`error`/`FAIL_AFTER_EMISSION` through a module-level `SCRIPTS` mapping; `validate_settings` accepts `{}`.

## Requirements

Plan mapping: R5, R1
Acceptance criteria owned by this step: AC31, AC50, AC54, AC57
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

[`tests/fixtures/modules/fakeplatform/__init__.py`, `tests/fixtures/modules/fakeplatform/module.yaml`, `tests/fixtures/modules/fakeeffects/__init__.py`, `tests/fixtures/modules/fakeeffects/module.yaml`, `tests/test_loader.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_loader.py`: discovery of `tests/fixtures/modules` yields exactly the 2 manifests; `fakeplatform`'s `chat.write` spec equals twitch's except for platform and `delivery_text_argument == "text"`; `fakeeffects` declares 3 actions, two with `delivery`; activating each on `runtime_context` binds the declared providers; `fakeplatform.inject` publishes one `channel.chat.message` with `platform == "fake"` and `metadata.schema_version == 2`; the scripted feed publishes on `start_inputs` and again on each `after_event`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (module boundary; fixtures must obey the same loader rules as shipped modules: immediate child directories, no symlinks). Two `chat.write` declarations (twitch and fake) coexist only because their destinations do not overlap — a fixture declaring `*/*/chat` would trip the ambiguity rule when both are enabled.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase1): ...`, `fix(phase1): ...`, `test(phase1): ...`, `refactor(phase1): ...`).
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
