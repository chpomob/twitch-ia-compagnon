# Brief — Phase 2 "audio and stream interaction" for twitch-ia-compagnon

## Context

Target repository (real git repo, clean tree, branch `main`): `/media/chpo/HDD-papa/twitch-ia-compagnon`.
Python (>= 3.10), single process, asyncio, modular event-bus architecture: `core/` (bus, loader,
lifecycle, admission, actions, contracts, triggers, attachments, context, runtime, supervision, main)
plus `modules/` (`brain`, `chat_context`, `users`, `capture`, `proxy`, `agent_link`, `twitch`, `audit`).

Current state: **phase 0 and phase 1 are delivered and gated** — `main` at `7e26038`, tree clean,
`.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` -> **1363 passed, 6 skipped**. Phase 1 gave
the multi-turn agentic loop, the single OpenAI-compatible adapter with capability probes and vision,
the configured/pluggable **delivery list**, the four read/write/capture capabilities, the PC agent and
its WebSocket JSON proxy with one agent per brain, and the two deployment profiles.

AUTHORITY DOCUMENTS (read them, do not re-derive):
- `/media/chpo/HDD-papa/twitch-ia-compagnon/docs/design-v2.md` — the approved v2 design. THIS PHASE IS
  SECTION 6, "Phase 2 — Audio et interaction stream" (deliverables + exit criteria), supported by
  section 4.4 (the seven starter modules and their exact tools/results), section 2.4 (configurable
  triggers per input, including the voice-input family), section 3.3 (observation and explicit terminal
  outcomes), 3.4 (budgets) and 4.3 (local provider or remote proxy, bounded attachment transfer).
- `/media/chpo/HDD-papa/twitch-ia-compagnon/docs/proxy-protocol.md` — the normative proxy protocol v1
  delivered in phase 1 (the phase-2 audio paths must conform to it, not extend it ad hoc).
- `/media/chpo/HDD-papa/twitch-ia-compagnon/docs/campaigns/phase1/{spec.md,plan.md}` — the phase-1
  specification and plan (the delivery list, capability probes, leases and supervision are already there).
- Existing code that this phase extends: the modules named above, `config*.yaml.example`, `tests/`.
- Historical (v1 MVP) documents, NOT the target: root `spec.md`, `plan.md`.

## Environment facts on the reference machine (design §4.4 requires honesty about this)

- A **local TTS/STT HTTP service is running** on `127.0.0.1:5050` (no auth). It is a candidate local
  speech provider; the boundary must stay configurable (endpoint + voice + model in module settings).
- **Playback tooling available**: PipeWire (`pw-cli`), `aplay`, `ffmpeg`. No PulseAudio server info.
- **OBS is NOT installed** on this machine and its websocket port (4455) is closed. Per design §4.4 a
  module whose external dependency is absent must remain **discovered but not ready**, with an honest
  capability view — never a fake readiness and never a hard failure of a chat-only profile.

## Goal

Produce a specification for PHASE 2 ONLY, derived from design section 6 Phase 2 and section 4.4's three
remaining starter modules (translate the design's exit criteria into English acceptance criteria, keeping
their substance):

1. **`modules/audio_output/`** — `audio.speak` (output/action: text + allowed voice/output → confirmed
   playback state) and `audio.play` (output/action: an audio reference → playback state). A TTS backend
   may be a local server or a remote service; the final playback happens on the output machine,
   possibly through the proxy. **Success means playback completed under the contract, not merely that
   synthesis was created.** `audio.speak` must be usable as a **delivery action** in phase 1's
   configured delivery list (mode `fixed` or mode `modules`), each entry declaring how the answer text
   maps into its arguments — no change to the agentic loop.
2. **`modules/audio_input/`** — `audio.capture` (input/observation: source + bounded duration →
   `audio_ref` plus optional dated transcription), acquisition near the device, bounded segment
   transport when the brain is remote, transcription local or remote when configured. On-demand capture
   driven by the brain, **never implicit background capture**; spontaneous voice input (design §2.4) is
   a distinct capability, not a side effect of this module.
3. **`modules/stream_control/`** — `stream.scene.set` (allowed scene → confirmed OBS state) and
   `stream.poll.create` (bounded question/options/duration → poll identifier and state), with distinct
   permissions and **mutual exclusion per shared resource**. Scenes go through a provider on the PC or
   through the proxy; polls use the platform API with state tracking where available. Absent OBS must
   leave the module discovered-but-not-ready, with the provider boundary still testable against a
   scripted provider.
4. **Audio capability of the single model adapter**: the required audio capability is verified like the
   phase-1 probes (structured output, vision) — a backend lacking a required capability is refused
   explicitly before a scenario runs, never a silent drop.
5. **The seven starter units enable/disable honestly**: a chat-only profile with none of the audio/OBS
   dependencies must start, run and pass; the runtime's capability view must reflect what is actually
   ready (not just what is configured).
6. **Packaging and per-provider integration trials documented** (design §4.4's closing requirement).

## Constraints that must hold

- Phase 0 and phase 1 guarantees stay in force: bounded admission, phase lifecycle, explicit terminal
  action outcomes (`success`/`refused`/`error`/`timeout`/`cancelled`/`external_unknown`), **default-deny
  authorization including reads**, bounded retention, one absolute startup/shutdown cleanup deadline,
  secret redaction in traces and loss diagnostics, one action per run turn, and an uncertain external
  outcome NEVER memorized as a confirmed effect.
- **One command / one effect per call**: a poll must never be created twice; a lost confirmation after
  an effect resolves to reconciliation or `external_unknown`, never a second creation nor a false
  confirmation.
- Contracts and `core/` stay **platform-neutral and provider-agnostic**: no OBS, Twitch, device or vendor
  name in `core/main.py` or in the brain; every new module is enabled/disabled through its manifest.
- Media are bounded: audio segments have duration/byte limits, references carry TTL, and transfer over
  the proxy uses phase 1's bounded by-reference mechanism (never a local path assumed usable remotely).
- Two concurrent sessions sharing a resource (audio device, scene, platform connection) respect the
  provider's serialization.
- Tests must not use positive-duration sleeps: inject the clock, the RNG and the transports. Run the
  suite with `.venv/bin/python -m pytest` (the system python lacks `aiohttp`).
- No model, provider, device or vendor name in requirements, acceptance criteria or target descriptions
  other than where the design itself names a provider family and the wording must stay neutral anyway.

## Implementation details to settle in the spec (with rationale, concrete and testable)

- the TTS provider contract (request/stream shape, voice selection, timeout, failure taxonomy) and where
  "playback completed" is observed;
- the capture segment format and bounds (the design's media rules: WAV, 16 kHz mono PCM are already the
  project's convention) and how `audio_ref` is leased, expired and cleaned;
- the transcription contract (optional, local or remote, dated, bounded) and its failure mode;
- the stream-control provider boundary that OBS (absent here) implements, plus the scripted provider the
  tests use, and how "not ready" is reported;
- the poll lifecycle and its reconciliation rule after a lost confirmation;
- how `audio.speak` enters the delivery list without touching the agentic loop.

## Deliverable

A `spec.md` for phase 2 with R/AC ids, the caller enumeration (which existing call sites/tests move), a
test-failure allowlist if any existing assertion must legitimately change, and target file descriptions.
The plan stage will turn it into sequential steps executed by the adversarial code loop.
