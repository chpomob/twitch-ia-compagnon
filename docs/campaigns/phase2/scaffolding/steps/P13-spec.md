# Step P13 — Platform poll services

Plan step `P13` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Twitch: `HELIX_POLLS_URL = "https://api.twitch.tv/helix/polls"`; `_TwitchPollService(module)` with `async create(channel_id, question, options, duration_seconds)` — one POST `{broadcaster_id: channel_id, title, choices: [{title}], duration}` with `_helix_headers()`; a transport failure before the request left raises `PollTransportError(sent=False)`; after it, a lost answer raises `PollTransportError(sent=True)`; a non-2xx raises `PollStatusError(status, message_sanitised)` (never the body); a 2xx returns `{poll_id, question, options[], started_at, ends_at, state}` from `data[0]` (malformed → `PollMalformedAnswer`) — and `async get(channel_id)` — one GET `?broadcaster_id=` returning the list of `{poll_id, question, options[], started_at, state}`, same failure classes. `activate` publishes it through `context.services.publish("poll", "twitch", service)` **only when the underlying registry is bound** — `getattr(context, "services", None)` is a facade whose `available` is true (P4; round 2, P2), never on the facade's mere presence: a default `RuntimeContext` (`services=None`) hands every module a facade with `available` false, so activation on it publishes nothing and succeeds, which keeps the caller table's claim that the existing direct `RuntimeContext(` constructions need no change; a `ServiceRegistryError` (duplicate key) is not caught and fails activation (AC25); nothing else in `chat.write` or the chat source changes and the manifest is untouched. Fixture platform: `ScriptedPollService` (decision 7) published under `("poll", "fake")` at `activate` under the same `available` guard, scriptable per channel for `create` and `get`, with `creates`/`gets` records and a `polls` table; `poll_service_for(...)`/`reset()` at module level; actions unchanged (`chat.write` only).

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC28, AC25
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

[`modules/twitch/__init__.py`, `tests/fixtures/modules/fakeplatform/__init__.py`, `tests/test_stream_control.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4, P5]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_stream_control.py` (section "platform poll services", added after the "service registry" section of P4): the twitch module activated through `_session_factory` with a scripted Helix transport — `create` issues exactly 1 POST to `HELIX_POLLS_URL` with the configured bearer token and client id headers, the body shape above, and returns the parsed poll; `get` issues exactly 1 GET; 403 → `PollStatusError(403)` with none of the body text; a raising transport before send → `sent=False`; a lost answer → `sent=True`; the token appears in no trace; the harness registry resolves `("poll", "twitch")` after activation and the fixture publishes `("poll", "fake")`. Default-context guard (round 2, P2) — the twitch module activated through `_session_factory` on `RuntimeContext(bus, actions, supervision, tasks).for_module("twitch")` (no `services`) succeeds with `context.services.available is False`, 0 publications and `chat.write` bound as before; the same for the fixture platform; both through `ModuleLoader` on such a context — which is exactly what `tests/test_loader.py::test_fakeplatform_chat_write_equals_twitch_except_for_the_platform` (unchanged; its `RuntimeContext(**fields)` carries no `services`) already does, so its continued pass is the loader-path proof; with a registry each module publishes exactly once.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (network boundary). The service classifies "sent" honestly: `aiohttp` raises `ClientConnectorError` before the request leaves and `ServerDisconnectedError`/timeouts after — the former is `sent=False`, everything else `sent=True`, the rule `chat.write` applies through `mark_emitted`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase2): ...`, `fix(phase2): ...`, `test(phase2): ...`, `refactor(phase2): ...`).
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
