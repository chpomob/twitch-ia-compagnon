# Step P14 — `modules/chat_context`: the `chat.read` action (R5; AC26, AC30)

Plan step `P14` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Manifest v2 (roles `[]`, `produces: []`, `consumes: []`, `settings_schema` accepting an empty object plus the reserved `limits`, `settings_validator: validate_settings`, `actions: [chat.read]`: read, permission `chat.read`, destinations `*/*/chat`, args `{limit: integer 1..50, required}`, result `{messages[]{author_id, message_id, text, observed_at}, observed_at, coverage{returned, retained, window_seconds, complete}}`, `timeout_seconds: 5`, `idempotency: natural`). `activate` reads `context.chat` (`ChatContext`) and the accepted `limits.chat_context` block (decision 4); `prepare` registers the provider and marks ready; the provider validates `limit` (executor's schema check gives `invalid_arguments` for 0 and 51), reads `chat.read(platform, channel_id, limit=max_messages)` for `retained`, the newest `limit` of them oldest-first, `observed_at = clock()`, `window_seconds = max_age_seconds`, `complete = returned == retained and retained < max_messages`; returns the mapping as `result` and one `text` part rendering the messages (author, dated, text) for the transcript. No grant; no platform name.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC26, AC30
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

[`modules/chat_context/__init__.py`, `modules/chat_context/module.yaml`, `tests/test_chat_context_module.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P8, P9]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_chat_context_module.py`: AC26 (limit 3 of 5 → newest 3 oldest-first, `retained 5`, `complete false`; limit 10 → 5, `complete true`; a channel at `max_messages` → `complete false`; `limit` 0 and 51 → `invalid_arguments`); AC30 (no rule → `refused not_authorized`, provider invoked 0 times, on `twitch/*/chat` and `fake/*/chat`); the `text` part equals the rendered messages; manifest test (v2 shape, action declaration, no grant); `grep twitch` on the module is 0 lines.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (module boundary). `ChatContext.read` re-applies age eviction, so `retained` must be read in the same call sequence as the page or the two counts drift; the `limits` block is required — a profile without `limits.chat_context` fails the validator naming the field.

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
