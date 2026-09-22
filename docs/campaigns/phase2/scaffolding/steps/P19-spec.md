# Step P19 — Profiles, packaging assertions, degraded and chat-only startup, examples and profile suites (R8, R9; AC30, AC31, AC32, AC33, AC43)

Plan step `P19` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

PC profile: `enabled_modules` `[twitch, chat_context, users, capture, audio_input, audio_output, stream_control, brain, audit]`; `audio_input` (`sources.mic: {kind: command, argv: [${AUDIO_RECORDER}, …]}`, `transcription {enabled: true, endpoint: "", model: "", api_key: ""}` (AC43: empty out of the box — no provider default; the operator sets endpoint/model/key, and an empty endpoint leaves the optional transcription skipped while the capture stays `success`), `required: false`), `audio_output` (`synthesis {endpoint: "", model: "", api_key: ""}` (AC43: empty out of the box — no provider default, so `audio.speak` is unbound and named not ready until the operator configures one), `voices`, `outputs.default {player: {argv: [${AUDIO_PLAYER}, …]}}`, `clips.chime {path: ${AUDIO_CHIME_PATH}}`, `required: false`), `stream_control` (`scenes {provider {kind: websocket, url: ${OBS_WEBSOCKET_URL}, password: ${OBS_WEBSOCKET_PASSWORD}}, allowed: [...]}`, `polls {enabled: false}` — decision 6, `required: false`), brain `capabilities.required` unchanged (`audio` shown commented), the single fixed `chat.write` delivery entry with an `audio.speak` entry commented, acted budgets unchanged, grants for exactly the nine provided actions, 0 literal secrets. Server profile: adds `stream_control` (scenes `none`, polls `enabled: true`) before `brain`, proxy `actions: [screen.capture, audio.capture, audio.speak, audio.play, stream.scene.set]`, grants for the nine names. Agent profile: `[capture, audio_input, audio_output, stream_control, agent_link]`, scenes `websocket`, polls disabled, `agent_link.actions` the five names, one agent-side rule per action for principal `brain`. `tests/test_examples.py`: rewrite `MODULE_NAMES`/`EXPECTED_MANIFESTS` to 11 manifests, `ENABLED_MODULES` and `PROVIDED_ACTIONS` to the R9 lists (the 7 allowlisted tests), keep every other assertion; add the chat-only startup check (AC30). `tests/test_profiles.py`: `SHIPPED_MANIFESTS` to 11 and `len(...) == 11` (allowlisted); `--check-config` on the three profiles TWICE: first with the phase 2 endpoint variables UNSET (AC43 — `audio.speak` and the transcription named not ready, 0 sockets opened, exit 0) and then with placeholder values exported (the bound path, 0 sockets opened, exit 0); AC31 degraded startup; AC32 binding assertions for the server profile.

## Requirements

Plan mapping: R8, R9
Acceptance criteria owned by this step: AC30, AC31, AC32, AC33, AC43
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

[`config.yaml.example`, `config.server.yaml.example`, `agent.yaml.example`, `tests/test_examples.py`, `tests/test_profiles.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P18, P17]

All dependencies are already merged into `main` when this step runs.

## Tests

AC43 — the shipped `config.yaml.example` and every audio-enabled profile carry an EMPTY speech and transcription endpoint: `audio.speak` is named not ready with a value-free reason, zero requests are attempted, startup succeeds, and the chat-only profile is identical (arbiter decision 22/09: no provider default). AC30 — the PC profile with `enabled_modules` reduced to the six phase 1 modules passes `--check-config` (exit 0), reaches readiness with 0 `module.degraded`, runs `chat.read` → final → `chat.write` with 1 send, `registered_ready()` is exactly `{chat.read, users.read, screen.capture, chat.write}` and the catalog declares 9 actions. AC31 — the full PC profile with every phase 2 endpoint at a closed loopback port (`127.0.0.1:1`, bounded on the clock), a non-executable player and no poll service reaches readiness with exactly 3 `module.degraded` (`audio_output`, `audio_input`, `stream_control`), runs the chat scenario with 1 send, lists none of the five phase 2 actions in `registered_ready()`, and the brain's authorized tools are the phase 1 read actions only. AC32 — each profile enables exactly R9's module list in order, references every endpoint and credential as `${NAME}` with 0 literal secrets, passes `--check-config` with exit 0 and 0 sockets, its grants equal its provided-action set (9, 9, 5); the two brain profiles keep the acted budgets and the single fixed `chat.write` entry; the server profile binds the five allowlisted actions to the proxy provider and `stream.poll.create` to `stream_control` with no ambiguity diagnostic. AC33 — `pyproject.toml` carries the three package-data lines, the install test discovers exactly 11 manifests, the runtime dependency list is `aiohttp`, `PyYAML` only.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (irreversible surface: the shipped profiles). The eight allowlisted tests are the only phase 1 assertions rewritten; `--check-config` validates the new modules' settings without building a session (their transport factories are called at `prepare` at the earliest).

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
