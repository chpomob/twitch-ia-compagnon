# Brief — Phase 5: Streamer.bot as an optional bridge (sink, triggers, and input replacement)

Project: `twitch-ia-compagnon` (repo `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`,
HEAD `9f6c1d9`).
Status before this phase: phases 0–4 delivered, 17 modules, 3,124 unit tests green, the local
configuration UI shipped and public. `main` is clean and pushed.

## Problem

A real streamer in the operator's circle already runs **Streamer.bot** (a free, closed-source,
Windows-first stream-automation application: 444 documented sub-action types, 444 trigger types,
~175 core C# methods). He wants to use the companion **through** it, and the companion currently
ignores it entirely: nothing reads what Streamer.bot knows, nothing can drive what Streamer.bot can
do.

Streamer.bot is genuinely strong exactly where the companion is thin — platform access and effects
(OBS scenes and sources, Stream Deck, Wave Link, VTube Studio, Voicemod, clips, moderation,
channel-point rewards, polls, predictions, raids, sounds, TTS through Speaker.bot) — and genuinely
weak exactly where the companion is strong: it has **no caller authorization at all** and **no
execution confirmation**. So the integration is not "adopt Streamer.bot as our engine"; it is
"expose Streamer.bot as one more optional capability behind our own policy and honesty contracts".

A complete technical study exists and is the ground truth for this brief:
`docs/research/streamerbot-etude-2026-10.md` (French, 42 registered sources in
`docs/research/sources-ledger-streamerbot.json`). Read it before specifying anything: it documents
the four interaction surfaces, the 22 WebSocket requests, the authentication scheme, the
`fire-and-forget` execution model, and the eight questions only a real instance can answer.

## Decisions already made by the operator (binding, and a specification that narrows them is wrong)

- **1 — Streamer.bot is optional, always, and its absence changes nothing.** Absent, disabled or
  unreachable, the companion behaves exactly as it does today: no required setting at startup, no
  change to any default path, no capability that a shipped profile depends on it for. A specification
  that makes Streamer.bot required anywhere — configuration, admission, readiness, a delivered
  profile, a test that cannot pass without it — is wrong and must be rejected.
- **2 — With Streamer.bot present, the minimum scope is the sink *and* the triggers, together.**
  The **sink**: the executor can run a named action of the user's Streamer.bot instance, with the
  companion's own authorization, argument validation, deadlines and terminal outcomes. The
  **triggers**: an event that Streamer.bot routes toward the companion enters it as an input that the
  existing trigger policies evaluate. Both belong to this phase; shipping one and deferring the other
  is a narrowing.
- **3 — Streamer.bot's events may *replace* our basic inputs when it is present.** Chat reading,
  viewer/user reading and presence are the families concerned. This is a **replacement, not an
  additional path**: what is replaced stops being active. The specification must design the ownership
  mechanism; what it may **not** do is let two providers ingest the same family of events in parallel
  and rely on the deduplication window to hide it, or leave the operator's configuration unable to
  say which provider owns which family.
- **4 — Do not hide the problems.** The operator's mandate for this phase is a **beautiful
  architecture that stays modular and adapts to the streamer's setup and use cases**, with the
  integration's real defects designed around rather than papered over. Concretely: the fact that
  Streamer.bot cannot confirm an execution must be visible in the contract the model reads and in
  what the operator sees — never converted into a success claim; a capability that cannot be trusted
  must be refusable or refusable-by-configuration; and no test may assert a behaviour the real
  Streamer.bot has not been observed to have.

## Requirements the specification must satisfy

Drawn from the study (`docs/research/streamerbot-etude-2026-10.md`, sections 4 and 6) and from this
repository's existing contracts. Each is a requirement to design, not an implementation choice to
copy.

- **R-A — Two roles, one optional source of capability.** The sink is an action provider in the
  existing action registry; the triggers are an input family in the existing trigger engine. The
  specification decides the module decomposition (one module or several) and must justify it against
  this repository's existing manifest/lifecycle/service contracts (`core/loader.py`, `core/actions.py`,
  `core/triggers.py`, `core/contracts.py`, `modules/*/module.yaml`). No name of Streamer.bot may
  appear in `core/`: the kernel stays platform-agnostic, exactly as it is for Twitch, Kick and
  YouTube today.
- **R-B — Input ownership is explicit and validated before any effect.** Introduce the notion the
  repository lacks today: a family of input events (chat messages, viewer/user reads, presence) has at
  most one active provider. A configuration that asks for two providers of one family must be
  **refused at preparation with a diagnostic naming the family and both providers**, the way an
  ambiguous action binding is already refused (`AmbiguousBindingError`). A configuration that leaves a
  family unowned must be either refused or defaulted by an explicit, documented rule — the
  specification chooses, but silently-both is excluded. The operator must be able to state the choice
  in a profile, and the configuration UI must be able to display it.
- **R-C — The sink's contract is honest about silence.** Streamer.bot's `DoAction` returns "request
  accepted" and nothing else; its HTTP endpoint answers `204` with no body. The action's declared
  argument/result schema, its `nature`, its `idempotency`, its destination scope and its terminal
  outcomes must make that visible: an execution whose success cannot be established is not a success.
  The specification defines the exact outcome vocabulary (the repository already has `accepted`,
  `refused`, `error`, `timeout` and `external_unknown`/`uncertain` in neighbouring actions — reuse it
  rather than inventing a dialect) and what the brain is told.
- **R-D — An allowlist, because Streamer.bot authorizes nobody.** The companion's default-deny governs
  everything; running "any action of the user's instance" must not become a single unconstrained
  capability. The specification defines how the operator restricts which Streamer.bot actions are
  reachable (by name, by group, discovered or declared), how a name outside the restriction is
  refused **before any request leaves the process**, and how the UI exposes it.
- **R-E — Triggers enter through a real ingress, not a special case.** An event routed by Streamer.bot
  must arrive through a mechanism the repository already sanctions or one the specification adds as a
  general capability: `docs/proxy-protocol.md` plus `modules/proxy`/`modules/agent_link` already carry
  an authenticated JSON WebSocket with a pairing token, and Streamer.bot can both connect out to such a
  server and receive connections on its own custom WebSocket server. The specification picks the
  ingress, states why, and keeps it transport-agnostic — no Streamer.bot-shaped branch in the brain.
- **R-F — Wiring and lifecycle follow the existing contracts.** Settings schema plus validator, declared
  credentials redacted from traces, declared lifecycle roles, readiness, degraded states, bounded
  deadlines, `CancelledError` handling and clean shutdown are the repository's existing rules and apply
  unchanged. A Streamer.bot instance that is not reachable at startup must produce a **ready** companion
  with an explicitly unavailable capability (or a refusal), never a failed startup — this is decision 1
  in its most testable form.
- **R-G — The unverifiable is declared, and the real trial is planned into the deliverable.** Eight
  questions in the study's section 9 cannot be answered by documentation (behaviour of `DoAction` on a
  nonexistent action, latency and blocking-queue behaviour, enforce-mode connection handling, minimum
  version per request, `System.Net.Http`/`Newtonsoft.Json` availability, the `Custom.Event` round trip,
  Kestrel under Wine, subscribed-event volume). The specification must define how the integration is
  exercised against a **real instance** (an opt-in trial node in the repository's existing style, plus
  a recorded trial file the README/docs can cite), and must state that no "delivered" claim is allowed
  until that record exists. It must equally define the fake-server tests that let the code be written
  before the trial.
- **R-H — Adaptability is a requirement, not a slogan.** The streamer's setup varies: same machine or
  a remote brain, one platform or several, Streamer.bot reading the chat or only executing actions. The
  specification must make every one of those combinations expressible by **configuration alone**, with
  no code change and no profile shipped in this repository depending on any of them, and must name for
  each combination what is enabled, what is disabled and what is refused.

## Constraints (repository rules that bind the specification)

- Artifacts in English; `R<n>:` and `AC<n> (R<n>):` identifier formats strictly respected; every
  requirement covered by at least one named, testable acceptance criterion.
- Enumerate **every** caller of anything whose signature changes. New APIs rather than migrations are
  expected here, but the loader, registry, trigger engine, configuration schema, overlay/UI and the
  example profiles all interact.
- No new runtime dependency: `aiohttp` and `PyYAML` are the declared runtime set. A WebSocket client is
  written against them.
- No model, provider or vendor name in requirements or acceptance criteria; no hardcoded endpoint.
- Never hardcode or assume a Streamer.bot version, port, password or action name in code or tests: all
  are configuration.
- The suite (3,124 tests today) must stay green, and the new behaviour must be covered by tests that do
  not need a live Streamer.bot.
- Documentation follows the repository's existing conventions: the operator-facing material is
  bilingual (`X.md` / `X.fr.md`, with the normative text identified), the recorded artefacts live under
  `docs/campaigns/…`, and a delivered phase ends with a full-branch review gate whose report is tracked.
- The specification must include an **Expected gains** section: quantified and mapped to acceptance
  criteria, in the style the operator requires (what the companion can do after this phase that it
  cannot do now, and what it costs).

## Explicit non-goals

- No reimplementation of Streamer.bot's features: OBS control, Stream Deck, Wave Link, VTube Studio,
  Voicemod, Speaker.bot and the rest stay Streamer.bot's job and are reached only through the sink.
- No requirement on Linux/Wine or macOS: Streamer.bot is Windows-first and its Linux support is
  experimental and unsupported. The companion stays host-agnostic; the bridge does not promise what
  Streamer.bot does not.
- No use of Streamer.bot's supporter-only features (cloud backups, custom webhooks, raised Deck limits)
  as a dependency.
- No per-token paid API path anywhere in the specification's validation story.

## Ground truth to read before writing

1. `docs/research/streamerbot-etude-2026-10.md` — the study, including section 10 (the retained
   decisions and the questions that remain open, each with a recommendation).
2. `docs/design-v2.md` — the normative design of the companion's kernel.
3. `core/actions.py`, `core/triggers.py`, `core/loader.py`, `core/contracts.py` — the contracts the
   sink, the input ownership rule and the lifecycle must respect.
4. `docs/proxy-protocol.md`, `modules/proxy/module.yaml`, `modules/agent_link/module.yaml` — the
   authenticated ingress that already exists.
5. `modules/twitch/module.yaml`, `modules/stream_control/module.yaml`, `modules/clips/module.yaml` —
   the manifest v2 shape a new module must follow (settings schema, validator, credentials, lifecycle
   roles, triggers, actions with destinations/idempotency/delivery).
6. `docs/README.md` — the repository's documentation and versioning conventions.
