# Brief — Phase 3 "présence, mémoire et plateformes" (twitch-ia-compagnon)

## Context

Real git repo, clean tree, branch `main`: `/media/chpo/HDD-papa/twitch-ia-compagnon`. Python, asyncio,
modular event-bus architecture: `core/` (bus, loader, lifecycle, admission, actions, contracts, runtime,
attachments, supervision, main) + `modules/` (`brain`, `chat_context`, `users`, `capture`, `proxy`,
`agent_link`, `twitch`, `audit`, `audio_output`, `audio_input`, `stream_control`).

**Phases 0, 1 and 2 are delivered and gated**: `main` is clean, the full suite is green
(`.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` — 1891 passed, 12 skipped), and there are
**12 modules**. Phase 1 gave the multi-turn agentic loop, the single OpenAI-compatible adapter with capability
probes (structured output, vision, audio), the configured delivery list, and the read/write/capture actions.
Phase 2 gave audio output/input, stream control (scenes through a `none`/`scripted`/`websocket`
obs-websocket provider, polls through a bounded runtime service registry with reconciliation), audio
references over protocol v1, profiles and the per-provider trial records.

AUTHORITY DOCUMENTS (read, do not re-derive):
- `docs/design-v2.md` — the v2 design. Its section 6 ends with "**Phase 3+ — seulement selon l'usage
  observé**", which is exactly the input this brief is built on.
- `docs/research/usage-createurs-assistant-ia-2026.md` — the demand research (sept. 2026): a 1 183-item
  public community dataset analysed locally, the competing offer and its pricing, and Twitch's own Stream
  Coach direction. **Every feature below exists because that report shows demand for it**; cite it where the
  spec justifies a scope choice.
- `docs/proxy-protocol.md` (protocol v1, normative), `docs/campaigns/phase{1,2}/{spec.md,plan.md}` — the
  delivered contracts that must not regress.

## Operator decisions (verbatim from the streamer, 2026-09-23) — these are REQUIREMENTS, not suggestions

1. **Scope**: "1 b + c" — deliver the **presence pack (b)** *and* the **viewer memory (c)**; clips were part of
   option (b) and are in scope.
2. **Moderation**: "2 c, mais ça devrait être configurable ça" — the model MAY ACT on moderation **under strict
   rules**, and the possible behaviours must be **configurable per streamer** (at minimum: alert only / propose
   and let a rule or the streamer apply / act under strict rules).
3. **Screen watching**: "3 b si demande, configurable aussi" — a continuous/"watch the game" capture is allowed
   **when requested**, as a **configurable trigger** (with its own cadence and budget), not as a default.
4. **Memory**: "4 a, avec durée et taille de mémoire configurable, un nombre de fichier de mémoire aussi
   configurable, et en cas de max, on analyse le moins utilisé/plus vieux pour supprimer" — default retention
   **30 days**; the **retention duration**, the **total memory size** and the **number of memory files** are all
   configurable; when a maximum is reached, the system **analyses** (least-used / oldest) and **evicts**.
5. **Platforms**: "5 b" — multi-platform adapters (Kick, YouTube) **now**, not later.

## Goal — the six workstreams

1. **Presence pack** — welcome new chatters/followers, thank subs/gifts/raids, "what did I miss?" chat summary,
   reply/translate for foreign viewers, polls, contextual scenes (starting soon / BRB / ending), the companion's
   voice, jingles. The point is that phases 1–2 already provide every primitive; what is missing is a
   **shipped, documented, tested configuration path** (example profile + triggers + prompts + a small
   integration test), not new capability. Deliver it as such, and say plainly in the docs which parts are
   configuration and which would need code.
2. **Live clips** — a new action to create a clip on the platform (the research ranks clips/highlights as the
   #1 creator-side demand). Decide the trigger surfaces (streamer command, chat redemption, a rule, the model
   proposing?) within the existing authorization model, the bounded result (clip id/state), and the failure
   taxonomy.
3. **Viewer memory** — remember regulars across runs ("it remembers me"), with the operator's configurability
   requirements: retention duration (default **30 days**), total size, **number of memory files**, and an
   **eviction policy that analyses least-used/oldest** to delete. Memory must stay an **optional module** (the
   core stays volatile and bounded, design §7), and must support erasure (per viewer / global) and honest
   limits.
4. **Configurable moderation** — the three behaviours above, expressed as module settings, with the rules that
   gate "act" mode (default-deny, audit of every action, bounded rate, no silent action), and the guarantee that
   the strictest setting is the shipped default.
5. **Configurable continuous capture** — an explicit trigger kind ("watch the game" at a configured cadence,
   with its own budget and its own authorization rule) that composes with the existing on-demand
   `screen.capture`; never enabled by default, never unbounded.
6. **Multi-platform adapters** — Kick and YouTube as platform modules speaking the same contracts as `twitch`
   (chat read/write, users where available, clips, polls where available), with an honest capability view when
   an API lacks a feature (quotas, missing endpoints), and no platform literal in `core/`.

## Constraints that must hold (phases 0–2 guarantees)

- Default-deny authorization for reads and writes; every new action needs an applicable rule and appears in the
  capability view.
- Bounded everything: admission, actions, budgets, retention, attachments, memory files, capture cadence.
- One action per run turn; explicit terminal outcomes; an uncertain external outcome is never memorized as a
  confirmed effect; one absolute cleanup deadline.
- The model proposes only what the policy allows; writes remain out of the model's toolset **except** where
  decision 2 above explicitly opens moderation under strict rules — that exception must be designed, not
  assumed.
- Platform-neutral core; vendor names confined to modules and the README.
- Tests: injected clock/RNG/transports, **no positive-duration sleeps**, run with the project virtualenv.
- Documentation: README phase 3 section in the phase 1/2 style, with the per-provider integration-trial records
  (which trials actually ran, which could not, and why).

## Implementation details the spec MUST settle (with concrete, testable choices)

- the **clip** action: platform API path, the trigger surfaces allowed, the bounded result and its failure
  codes, and what the audit records;
- the **moderation modes**: exact settings shape, what "act" permits (which platform operations), the rule
  constraints (rate limits, target scope, refusal paths), the audit trail, and the shipped default;
- the **memory model**: what a "memory file" is, its format and bounds, the retention/eviction algorithm
  (least-used/oldest analysis — state the observable rule the tests pin), the erasure paths, and how memory is
  surfaced to the brain under a bounded context budget;
- the **continuous capture trigger**: settings, cadence limits, budget accounting, how it interacts with the
  existing on-demand capture, and what the streamer sees in the runtime capability view;
- the **platform adapters**: which capabilities each of Kick and YouTube can honestly provide (with the API
  limits named), the authentication shape, the rate/quota handling, and the degradation policy;
- the **presence pack**: exactly which parts are configuration (shipped example profile + triggers + prompts)
  versus code, and the integration test that proves the path end to end.

## Deliverable

A `spec.md` for phase 3 with R/AC ids, the caller enumeration (which existing call sites/tests move), a
test-failure allowlist where an existing assertion must legitimately change, and target file descriptions.
The plan stage then turns it into sequential steps for the adversarial code loop (Claude DEV + Codex REVIEW,
one atomic commit + green tests per step).
