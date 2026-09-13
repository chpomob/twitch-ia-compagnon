# Brief — Phase 1 "agentic text + image vertical, local and remote" for twitch-ia-compagnon

## Context

Target repository (real git repo, clean tree, branch `main`): `/media/chpo/HDD-papa/twitch-ia-compagnon`.
Project: an AI companion for streamers. Python (>= 3.10), single process, asyncio, modular event-bus
architecture: minimal `core/` (bus + loader + lifecycle + admission + actions + supervision + main) plus
modules under `modules/` (today: `twitch`, `brain`, `audit`). Runtime dependencies today: `aiohttp`,
`PyYAML` only (`pyproject.toml`).
Current state: HEAD `caf09e9`, working tree clean, `python3 -m pytest tests/ -q -p no:cacheprovider`
-> **738 passed**. Phase 0 (the coherent foundation) is delivered and has passed a full-branch gate
(24 steps, findings F1–F9 + N1–N7 all fixed with tests).

AUTHORITY DOCUMENTS (read them; do not re-derive):
- `/media/chpo/HDD-papa/twitch-ia-compagnon/docs/design-v2.md` — the approved v2 design. THIS PHASE IS
  SECTION 6, "Phase 1 — Verticale agentique texte + image, locale et distante" (deliverables + exit
  criteria), supported by section 3.1 (loop and task ownership), 3.2 (action and call contract),
  3.3 (observation and explicit terminal outcomes), 3.4 (admission, sessions and budgets), 4.1
  (versioned manifest and activation), 4.2 (phase lifecycle), 4.3 (local provider or remote proxy) and
  4.4 (the seven starter modules).
- `/media/chpo/HDD-papa/twitch-ia-compagnon/docs/README.md` — step/commit/validation index.
- Existing code that this phase extends: `core/{bus,loader,lifecycle,admission,actions,contracts,triggers,attachments,context,runtime,main}.py`,
  `modules/{twitch,brain,audit}/__init__.py` + `module.yaml`, `config.yaml.example`, `tests/`.
- Historical (v1 MVP) documents, NOT the target: `spec.md`, `plan.md` at the repo root.

## Goal

Produce a specification for PHASE 1 ONLY. Scope, derived from design-v2 section 6 Phase 1 (which also
lists its exit criteria — translate them into English acceptance criteria, keep their substance):

1. **Agentic multi-turn loop in the brain**: the run engine must iterate model turns; the model
   proposes one action per turn, the executor runs it, the observation (text or image) returns to the
   loop, and the final answer is delivered. Phase 0's single-turn engine is the starting point.
2. **One single OpenAI-compatible Chat Completions adapter** with structured output/tool calls and
   vision. Endpoint, model and key stay configurable. A backend lacking a required capability
   (structured output, vision) must be REFUSED explicitly before the scenario runs — never a silent
   drop of an image or of an expected structure. No multiple proprietary adapters in v1.
3. **Acted budgets from section 3.4** enforced on turns, repeated actions, tokens, model wait, action
   wait, total deadline, delivery deadline and saturation, with the decided fallback policy
   (generic message enabled by default, disableable, subject to authorization, consuming the output
   budget; silence when disabled or when the bounds forbid a send).
4. **Multimodal observations and an operational attachment store**: image references with bounded
   size/TTL, cleaned after the run, transferred by reference through the remote proxy — never a local
   path assumed usable remotely.
5. **Four usable modules/capabilities**: `chat.read`, bounded `users.read` (with honest pagination,
   freshness and coverage — a partial list must never be presented as a complete audience),
   `chat.write`, and one simple capture (`screen.capture`).
6. **The small PC agent and its proxy**: a real second process exposing local capabilities to the
   brain over a WebSocket JSON protocol with a pairing token, exactly ONE agent per brain in v1,
   reconnection with backoff, and WITHOUT distributing `EventBus` (remote inputs use a source adapter
   that authenticates and normalizes events before publishing into the local bus; the bus is not
   exported over the network).
7. **Two distinct deployment profiles**: (a) brain + platforms/sinks on the streamer's PC; (b) brain
   and platforms/sinks on a remote server with local capabilities exposed by the PC agent. A single
   `run()` must work on both; the capture branch must not leak into the brain.
8. **Installable modules/manifests** for the above, discoverable in a clean environment.

## Implementation details the design delegates (settle them in the spec, with rationale)

Design section 6 says the following are "to be specified at implementation time". The spec must
PROPOSE a concrete, testable choice for each, not leave it open:
- the exact message format/envelope of the WebSocket JSON proxy (request/response/observation frames,
  correlation ids, error frames, versioning);
- the syntax of the two deployment profiles and how the brain selects its transport provider;
- the backoff parameters for reconnection, and what happens to in-flight work when the proxy drops
  (including a drop AFTER an effect was emitted — never a blind retry);
- how the adapter VERIFIES structured-output/tool and vision capabilities before a scenario, and how
  it reports a refusal.

## Constraints that must hold

- Preserve the 738 existing tests unless the design explicitly changes a guarantee; phase 0's
  documented guarantees stay in force (bounded admission, phase lifecycle, terminal action outcomes,
  default-deny authorization, bounded retention, single global startup/shutdown deadline, redaction of
  configured secrets in traces and loss diagnostics).
- Default-deny stays: no applicable rule => `refused`, reads included; grants are configured explicitly
  by tests and profiles (`chat.read`, `users.read`, `screen.capture`, `chat.write`).
- No module name may be added to `core/main.py`; a new module must start and stop through its manifest
  (validated by phase 0's own acceptance test).
- Platform neutrality: nothing in the contracts or the brain may depend on Twitch specifically; a
  second fake platform must exercise the same contracts.
- One action per turn in phase 1: a backend response containing several calls is refused as an
  unsupported shape, with no partial effect.
- An uncertain external outcome is never memorized as a confirmed send.
- Tests must not use positive-duration sleeps: inject the clock, the RNG and the transports (this is
  an explicit standing constraint, enforced by a whole-suite grep).
- No hardcoded model names or model-specific commands anywhere in the spec's requirements or targets.

## Deliverable

A `spec.md` for phase 1 with R/AC ids, the caller enumeration (which existing call sites/tests move),
a test-failure allowlist if any existing assertion must legitimately change, and target file
descriptions. The plan stage will turn it into sequential steps executed by the adversarial code loop.
