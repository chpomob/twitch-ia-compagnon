---
name: "phase5-automation-bridge"
version: "1.0"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec, phase5, automation-bridge, input-ownership]
targets:
  - file: core/contracts.py
    description: "Add the closed input-family vocabulary (`chat`, `viewers`, `cues`), the `cue` event kind (excluded from the platform-notice kinds), the manifest input-family declaration contract and the owner literal `none`; no vendor name."
  - file: core/loader.py
    description: "Parse and validate the optional `input_families` manifest key; resolve input ownership at preparation from the enabled manifests and the profile's `inputs` block, refusing double ownership, invalid owners and unknown families before any module is prepared; hand each module its owned (family, platform) view; bind a family-gated action only for the platforms its module owns."
  - file: core/runtime.py
    description: "Expose the resolved ownership as a read-only, additive view on the per-module context; drop, count and diagnose any chat-family or cue event a module publishes for a (family, platform) pair it does not own, before any subscriber sees it."
  - file: core/main.py
    description: "Accept and validate the optional top-level `inputs` block (load_config, run, check_config, --check-config), so ownership diagnostics are reported without opening any transport."
  - file: core/config_ui/__init__.py
    description: "Render the resolved input-ownership table read-only on the core-settings page (owner, explicit or by default rule, every candidate) and show ownership diagnostics through Check; names no module."
  - file: modules/twitch/module.yaml
    description: "Declare `input_families: [{family: chat, platforms: [twitch], default: true}]`."
  - file: modules/twitch/__init__.py
    description: "Open chat/notice ingestion only for owned pairs; keep `chat.write` and its readiness when chat ingestion is owned elsewhere."
  - file: modules/kick/module.yaml
    description: "Declare `input_families: [{family: chat, platforms: [kick], default: true}]`."
  - file: modules/kick/__init__.py
    description: "Accept and publish webhook chat/notice events only for owned pairs; keep `chat.write` when chat ingestion is owned elsewhere."
  - file: modules/youtube/module.yaml
    description: "Declare `input_families: [{family: chat, platforms: [youtube], default: true}]`."
  - file: modules/youtube/__init__.py
    description: "Poll and publish chat events only for owned pairs; keep `chat.write` when chat ingestion is owned elsewhere."
  - file: modules/users/module.yaml
    description: "Declare `input_families: [{family: viewers, platforms: ['*'], default: true}]`; widen the `users.read` coverage.kind enum to `observed_authors` and `active_viewers` with a description that fits both providers."
  - file: modules/users/__init__.py
    description: "Serve `users.read` only for the platforms it owns; never record a `cue` event's author in the directory; still report `observed_authors`."
  - file: modules/brain/__init__.py
    description: "Treat the `cue` kind like a platform notice for the route-safety rule and the empty-text rule (a trusted-origin event), so a cue route may hold an effect delivery."
  - file: modules/streamerbot/module.yaml
    description: "New manifest v2 of the bridge: settings schema with titles/defaults, validator, declared credential, `input` role, `input_families` (chat/viewers non-default, cues default), trigger declaration, the `automation.action.run` write action and the shared `users.read` declaration."
  - file: modules/streamerbot/__init__.py
    description: "New bridge module: one outbound authenticated WebSocket client written on aiohttp, allowlist/inventory reconciliation, the run-action provider and its outcome vocabulary, owned-family event mapping, cue envelope mapping, the bridge-backed `users.read`, bounded queues, readiness, reconnection and shutdown."
  - file: pyproject.toml
    description: "Add the `\"modules.streamerbot\" = [\"module.yaml\"]` package-data line; runtime dependencies stay exactly aiohttp and PyYAML."
  - file: tests/test_input_ownership.py
    description: "New: ownership resolution, refusals naming family/platform/both providers, `none` owner, wildcard fallback, runtime drop of non-owner events, family-gated bindings, no-profile-change parity."
  - file: tests/test_automation_bridge.py
    description: "New: fake-instance-server tests of authentication, readiness, unreachable-at-startup, allowlist and inventory refusals with zero frames sent, the full outcome table, event mapping, cues, bridge `users.read`, bounded queues, reconnection, cancellation and shutdown, credential redaction."
  - file: tests/fixtures/automation_bridge/
    description: "New: the fake server's frames, each carrying a provenance (`documented` with a sources-ledger id, or `observed` with a trial-record reference)."
  - file: tests/test_phase5_trials.py
    description: "New: opt-in real-instance trial nodes for the eight study questions, skipped with a reason naming their variables by default."
  - file: tests/test_contracts.py
    description: "Adapt the event-kind vocabulary and platform-notice assertions to the added `cue` kind (allowlisted)."
  - file: tests/test_examples.py
    description: "Adapt MODULE_NAMES, EXPECTED_MANIFESTS, DECLARATION_KEYS and DECLARED_ACTIONS to the 18th module and the `input_families` key (allowlisted)."
  - file: tests/test_manifest_presentation.py
    description: "Adapt the shipped-manifest count from 17 to 18 (allowlisted)."
  - file: tests/test_profiles.py
    description: "Adapt the shipped-manifest and package-data counts from 17 to 18 (allowlisted)."
  - file: tests/test_users.py
    description: "Adapt the exact coverage.kind enum assertion to the widened enum (allowlisted); the module still reports `observed_authors`."
  - file: docs/automation-bridge.md
    description: "New operator guide (English, normative): what the bridge does and does not, settings, allowlist, ownership, the outcome vocabulary, the combination table of R9, the cue recipe, the trial status."
  - file: docs/automation-bridge.fr.md
    description: "New French translation of the operator guide, stating that the English text wins where they differ."
  - file: docs/design-v2.md
    description: "Add an input-ownership addendum (family vocabulary, resolution rule, runtime guard) to the normative kernel design."
  - file: docs/design-v2.fr.md
    description: "Translate the input-ownership addendum."
  - file: docs/README.md
    description: "List the bridge guide in both languages and add the phase 5 trial row whose status is not delivered until the trial record exists."
  - file: README.md
    description: "Add the bridge to the module list with the status `not delivered — awaiting real-instance trial` until the trial record exists."
---

# Phase 5 — an optional bridge to a stream-automation application: sink, triggers and input replacement

READ: /home/chpo/.hermes/cache/scratch/adversarial-spec-write-8mko42qs/brief.md

## Problem

A streamer in the operator's circle already runs a stream-automation application
(Streamer.bot, studied in `docs/research/streamerbot-etude-2026-10.md`): 444 sub-action types,
444 trigger types, platform access he already configured. The companion ignores it entirely —
nothing reads what the instance knows, nothing can drive what it can do. The application is
strong exactly where the companion is thin (effects, platform access) and weak exactly where the
companion is strong: it authorizes no caller and confirms no execution (`DoAction` answers
"request accepted"; its HTTP surface answers `204` with no body).

The repository also lacks a notion this integration needs: today every enabled module that
produces `channel.chat.message` ingests in parallel, and the dedup window is the only thing
standing between two providers of the same stream and duplicate runs. Nothing lets the operator
say "this provider owns Twitch chat, that one does not".

This phase adds one optional bridge module — a sink in the action registry, a trigger input in
the trigger engine, and a provider that may *replace* basic inputs — behind the companion's own
default-deny, deadlines and honest terminal outcomes, and adds the kernel's missing input
ownership rule. Absent, the companion is exactly what it is today.

### Assumptions (documented, made where the brief is silent)

- A1. Naming. *The instance* is a running stream-automation application instance. *The bridge*
  is the new module `modules/streamerbot` (its module name is `streamerbot`; that name never
  appears in `core/`, nor in any requirement or criterion below). *The study* is
  `docs/research/streamerbot-etude-2026-10.md` and *the sources ledger* its registry
  `docs/research/sources-ledger-streamerbot.json`. *The guide* is the operator guide
  `docs/automation-bridge.md` (English, normative) with `docs/automation-bridge.fr.md`. *The
  fixtures* are the files under `tests/fixtures/automation_bridge/`. *The trial record* is
  `docs/campaigns/phase5/bridge-trial-record.md`. *The run
  action* is the bridge's write action, named `automation.action.run` (vendor-free), permission
  `automation.run`, destination scope `automation`.
- A2. Decomposition: **one module**. The sink, the trigger input and the replacement providers
  share one authenticated connection, one readiness state and one credential; splitting them
  would require a cross-module connection service and would let one half report ready while the
  connection it depends on is down. The existing contracts support one module holding the
  `input` role, declared actions and a trigger declaration (the `twitch`, `kick` and `youtube`
  modules already do). Placed in an agent process (beside `agent_link`), only the run action is
  reachable from the brain, through the proxy's existing actions allowlist; families and cues
  are not carried by proxy protocol v1 (its event allowlist is `agent.status` only) and this
  phase does not change the protocol (see R9).
- A3. Ingress choice: **the companion dials the instance's own WebSocket server** (the
  companion is the client; the same connection carries requests, acknowledgements, subscribed
  events and the custom events that carry cues). Rejected alternatives, and why: (a) the
  existing proxy — it accepts exactly one paired agent per brain (`agent_limit`), requires the
  `hello`/`welcome`/`session_id` exchange the instance cannot perform without user-written code,
  and its event allowlist would have to admit input events, a protocol change; (b) the
  instance's custom WebSocket server — it documents no authentication. The instance's main
  WebSocket server has a documented salt/challenge authentication, so this choice opens no new
  listening port in the companion and keeps one authenticated channel. The remote-brain
  topology is served by pointing `url` at the instance's host over `wss://` (TLS front, e.g. the
  instance's documented Tailscale Serve recipe). The ingress is transport-agnostic for the brain:
  the bridge publishes ordinary normalised events and the brain never learns their transport.
- A4. Input families (closed vocabulary, kernel-defined, vendor-free):
  - `chat` — ingestion of `channel.chat.message` events of every kind except `cue` (ordinary
    messages and platform notices), per platform;
  - `viewers` — the viewer/user read `users.read`, per platform. In this repository the only
    viewer read and the only presence view are the same capability (`users.read` lists who was
    seen recently with `first_seen`/`last_seen`/freshness), so "viewer/user reading" and
    "presence" of the brief are one family here; the bridge's provider of it answers from the
    instance's active-viewer presence and says so (`coverage.kind: active_viewers`). Adding a
    separate presence capability that replaces nothing would be a free widening (study §10,
    open question 6, recommendation a);
  - `cues` — events an operator automation deliberately routed to the companion (kind `cue`),
    per platform. A distinct family so routed triggers never compete with chat ownership.
- A5. Event kind `cue`: added to the closed `EVENT_KINDS` vocabulary (it names what happened —
  an operator automation routed a cue — not which source produced it), excluded from
  `PLATFORM_NOTICE_KINDS` (whose 7 members are unchanged), and treated by the brain like a
  notice for route safety and empty text, because its origin is the operator's own instance over
  an authenticated connection. Body text never yields a trusted role claim.
- A6. Confirmation round trip (custom-event response correlation, study §6.2) is **not** in this
  phase: it is the subject of trial question 6 and requires the user's actions to be adapted. Its
  absence is made visible, not hidden: the run action never reports `success` (R4). Sending chat
  through the instance is not in this phase either: replies stay with the platform modules
  (study §10 open question 2); a platform whose chat is owned by the bridge and whose platform
  module is disabled has no `chat.write` provider, which the executor already reports as
  `refused no_provider`.
- A7. Allowlist identity is the instance action **name** (what the user sees in the instance's
  UI) or **group**; both are configuration. The live instance inventory is reconciled at every
  connection and reported in a value-free diagnostic; the configuration UI, which opens no socket
  (phase 4 R5), shows the configured allowlist through the module's generated page, not the live
  inventory (recorded limit).
- A8. The configuration UI displays ownership **read-only** in this phase (it is not added to the
  phase-4 writable scope): changing an owner changes which module ingests, and the profile stays
  the place where it is stated.

Requirement classification: R1–R8 are non-trivial (8, at the cap); R9 (documentation of the
combinations, verified by check-config) and R10 (static constraints) are trivial.

## Requirements

- R1: The bridge is optional, always. With the bridge not enabled, every shipped profile, every
  default and every observable behaviour is unchanged: no new required setting, no new required
  top-level key, no change to any shipped `*.example` profile, and no shipped profile enabling the
  bridge or naming it. Enabled, the bridge has no setting that makes its unavailability fail
  startup: an instance unreachable, refusing authentication, or answering malformed frames at
  startup yields a companion that reaches readiness, a `module.degraded` record for the bridge
  with a value-free reason (`unreachable`, `authentication_failed`, `authentication_required`,
  `protocol_error` or `inventory_unavailable`), the run action and the bridge's `users.read`
  refused `provider_not_ready` with 0 frames sent, and nothing ingested for the pairs the bridge
  owns. No other provider takes over a pair the bridge owns when the bridge is down (no automatic
  fallback); the degraded state is the visible consequence.
- R2: Input ownership is explicit and validated before any effect. A manifest may declare the
  optional `input_families` key: a list of entries, each a family of the A4 vocabulary, a
  platform list (concrete names or the single wildcard `*`) and a boolean `default`. A profile
  may carry the optional top-level `inputs` block mapping family → platform (or `*`) → owner,
  where an owner is one enabled module name or the literal `none` (deliberately unowned).
  Preparation resolves exactly one owner (or none) for every (family, platform) pair: an explicit
  entry wins (the platform's own key, else that family's `*` key); without one, the single
  enabled module declaring the pair with `default: true` owns it; with none, the pair is unowned.
  Preparation is refused — before any module is prepared and before any transport opens, through
  `run` and `--check-config` alike — with a diagnostic naming the family, the platform and both
  providers when two or more enabled modules declare a pair with `default: true` and no explicit
  entry decides it, or when an owner value lists two or more modules; and with a diagnostic naming
  family, platform and value when an owner names a module that is not enabled or does not declare
  that family for that platform, when a family is outside the vocabulary, or when a manifest's
  declaration is malformed. Each module can read, from its runtime context, exactly the pairs it
  owns. The configuration UI's core-settings page shows, read-only, every resolved pair with its
  owner, whether it came from an explicit entry or the default rule, and every candidate; Check
  reports ownership diagnostics. No name of the bridge, or of any platform, appears in `core/`
  for this rule.
- R3: Ownership holds at runtime, without relying on deduplication. A module that does not own a
  (`chat`, P) pair opens no ingestion for P (no subscription, no poll, no accepted webhook
  publication for that pair) while keeping its declared actions and their readiness. Any
  chat-family or cue event a module publishes for a pair it does not own reaches no subscriber
  (not the trigger engine, not `users`, not `chat_context`, not the brain), increments the
  diagnostic counter `input_ownership_rejections` by 1, and produces one value-free diagnostic
  per (module, family, platform), never per event. Compatibility rule for manifests that declare
  no `input_families` key (undeclared publishers): an event an undeclared publisher publishes for
  a pair is delivered exactly as today when that pair has no resolved owner by the default rule,
  and is rejected as above only when the pair has a resolved owner (another module, by explicit
  entry or default) or is set to `none` by an explicit `inputs` entry. A module that declares the
  key is subject to the rule above for every pair, declared or not. Since no shipped profile
  carries an `inputs` block and no undeclared shipped module's pairs gain an owner unless the
  operator enables the bridge for them, R1's unchanged-behaviour promise holds. A family gated by an action (`viewers`) is
  bound only for the platforms its module owns: the owner of `*` serves every platform no explicit
  entry assigns elsewhere and never an assigned one, and no two bindings of `users.read` overlap
  (the existing `AmbiguousBindingError` stays unreachable for a valid ownership).
- R4: The run action's contract is honest about silence. The run action is declared with
  `nature: write`, `idempotency: none`, permission `automation.run`, one wildcard destination of
  scope `automation`, the effect-only delivery capability (`text_argument: none`), not
  model-proposable, arguments `{action: string (1–128 chars), arguments: object}` with only
  `action` required and no other key, and a `timeout_seconds` no smaller than the bridge's
  acknowledgement deadline. Its description, read by the model, states that the instance's
  acceptance is not a confirmation that the action ran. Its terminal outcomes, built only from the
  existing `TERMINAL_STATUSES` and executor codes, are exactly the table below; it never returns
  `success`, never retries, and sends at most one request frame per call.

  | Situation | status | error.code | error.cause |
  | --- | --- | --- | --- |
  | name/group not in the allowlist | `refused` | `not_allowlisted` | — |
  | allowlisted, absent from the instance inventory | `refused` | `instance_action_missing` | — |
  | allowlisted name matches two or more instance actions | `refused` | `instance_action_ambiguous` | — |
  | instance action disabled in the inventory | `refused` | `instance_action_disabled` | — |
  | `arguments` violate the entry's argument schema | `error` | `invalid_arguments` | — |
  | bridge not ready (existing executor rule) | `refused` | `provider_not_ready` | — |
  | instance acknowledged the request | `external_unknown` | `external_effect_unknown` | `execution_unconfirmed` |
  | instance answered an error | `error` | `provider_failed` | `instance_rejected` |
  | request sent, no acknowledgement before the deadline, or connection lost or call cancelled after sending | `external_unknown` | `external_effect_unknown` | `acknowledgement_lost` |
  | deadline or cancellation before the frame left the process | `timeout` / `cancelled` | existing codes | — |

  Every non-success observation carries `retryable: false`, and the `execution_unconfirmed` and
  `acknowledgement_lost` messages say in words that the companion does not know whether the
  action ran.
- R5: An allowlist, because the instance authorizes nobody. The bridge's `allowlist` setting is a
  list of entries, each naming exactly one instance action (`action: <name>`) or one instance
  group (`group: <name>`), with an optional per-entry `arguments` schema (the phase-4 schema
  subset); an entry without one accepts only an empty `arguments` object. Two `action:` entries
  with the same name, or two `group:` entries with the same name, are refused by the settings
  validator. When an instance action is matched by several entries, the schema that decides is:
  its `action:` entry's schema alone if one names it (group entries are then not consulted);
  otherwise the arguments must satisfy the schema of every matching `group:` entry (any one
  refusal yields `invalid_arguments`). An empty or absent
  allowlist makes every run refused `not_allowlisted`. Every refusal of the R4 table above the
  "bridge not ready" row, and every argument violation, is decided **before any frame leaves the
  process** (0 request frames). At every successful connection the bridge reconciles the
  allowlist against the instance inventory (the reachable set is the configured names, plus every
  instance action of a configured group, that exist exactly once and are enabled) and emits one
  value-free diagnostic listing the unmatched entries by their configured name; an inventory it
  cannot read leaves the bridge not ready. The documentation states that a group entry delegates
  the choice of reachable actions to whoever edits that group in the instance. The companion's
  authorization rules (`actions:` rules granting `automation.run`) still apply first, unchanged.
- R6: One authenticated connection under the existing lifecycle rules. The bridge holds at most
  one connection to the configured `url` (required; `ws://` or `wss://`; a non-loopback host
  without `wss` refused at validation, as `agent_link` does). It declares a settings schema with
  a `title` on every node and machine-readable defaults, a settings validator returning
  value-free per-field diagnostics, `password` as a declared credential (redacted from every
  trace and log; the profile references it as `${NAME}`), the `input` lifecycle role, and bounded
  deadlines for connect, authentication, each request and the acknowledgement (each a finite
  positive number, no default endpoint, port, password, version or action name anywhere in code
  or tests). It authenticates with the instance's documented challenge scheme when the instance
  requires it. It is ready only while connected, authenticated and holding a reconciled inventory;
  a lost connection makes it not ready at once (a `module.degraded` record), it reconnects with
  bounded backoff (minimum and maximum delays configured), and a reconnection that succeeds marks
  it ready again (a `module.ready` record) — never silently. Events published by the instance
  while disconnected are lost and the documentation says so. It subscribes only to the event
  categories the pairs it owns need; inbound events wait in a queue bounded by a configured count,
  and an event arriving at a full queue is dropped and counted, never blocking the connection.
  `CancelledError` propagates; shutdown closes the connection within the existing close budget,
  and in-flight run calls end with the R4 outcome for their state.
- R7: Triggers and replaced inputs enter as ordinary normalised events. (a) **Cues**: a custom
  event from the instance carrying the cue envelope (`cue_id`: non-empty, ≤ 128 chars; `platform`:
  non-empty; optional `channel_id`, defaulting to the instance's broadcaster account on that
  platform; optional `user_id` with `user_name`; `text`: a string of at most 500 code points) is
  published, when the bridge owns (`cues`, platform), as a schema-version-2 `channel.chat.message`
  with `payload.kind: cue`, `metadata.source` = the bridge's module name, `payload.message_id` =
  `cue_id`, the author = the envelope user, or, absent one, one stable instance identity distinct
  from every configured companion identity; an envelope that is malformed or oversized is dropped
  and counted, never truncated or repaired. Cues are evaluated by the trigger policies configured
  under `triggers.<bridge module name>`; the bridge's default policy accepts every cue and applies,
  to chat-family messages, the companion-mention rule the native chat inputs default to. (b)
  **Chat**: for each (`chat`, P) it owns, the bridge maps the instance's documented chat-message
  and notice events of P to normalised events with the same payload fields and kinds the native
  input of P produces; owning a (`chat`, P) pair requires a configured companion identity for P
  (anti-echo), else preparation is refused naming the platform; role claims from the instance are
  trusted only when `trust_instance_roles` is true (default false), with provenance naming the
  bridge; an instance event with no documented mapping is dropped and counted. (c) **Viewers**:
  for each (`viewers`, P) it owns, the bridge's `users.read` (the same declared spec as the
  `users` module's) answers from the instance's active-viewer list of P's broadcaster channel with
  `coverage.kind: active_viewers` and `coverage.complete: false`, and is refused
  `unsupported_destination` for any other channel.
- R8: The unverifiable is declared, and the real trial gates the "delivered" claim. Every frame
  the fake-instance tests replay is stored in a fixture carrying a provenance — `documented` with
  an existing id of the sources ledger, or `observed` with a reference to the trial record — and a
  fixture that exercises an instance behaviour the study lists as unverified (section 9) names
  that question number and must be `observed`; no test asserts such a behaviour from any other
  fixture. The bridge does not depend, for any refusal it
  makes, on how the instance answers a request naming a nonexistent action (question 1). Eight
  opt-in trial nodes, one per study question (1 nonexistent-name behaviour, 2 latency and blocking
  queue, 3 enforce-mode authentication, 4 minimum version per request, 5 C# HTTP/JSON availability,
  6 custom-event round trip, 7 servers under Wine, 8 subscribed-event volume), are skipped by
  default with a reason naming their variables; nodes 5 and 7 record an operator observation
  rather than a probe result. The trial record answers the eight questions with the instance version and the date; until it exists, the README
  and the docs index state the bridge as not delivered, and no document calls it delivered. The
  phase ends with a full-branch review gate whose report is tracked under `docs/campaigns/phase5/`.
- R9: Every setup is expressible by configuration alone. The guide gives, for each combination below, the profile fragment and what is enabled,
  disabled and refused; each fragment, inserted into a copy of a shipped profile with its
  `${NAME}` references set, passes `--check-config` (or, for the refused rows, fails it with the
  stated diagnostic). Combinations: (1) instance absent — bridge not enabled; (2) same machine,
  sink only (`inputs.cues.'*': none`); (3) same machine, sink and cues; (4) same machine, the
  instance reads Twitch chat, the `twitch` module stays enabled for `chat.write`; (5) several
  platforms, the instance owns one platform's chat and a native module another's; (6) the
  instance owns `viewers` for a platform; (7) remote brain, bridge on the brain host over `wss://`;
  (8) bridge in the agent process, sink only, reached through the proxy's actions allowlist;
  refused: (9) two owners for one pair, (10) an owner that is not enabled, (11) a non-loopback
  `ws://` URL, (12) the bridge owning a chat pair without a companion identity for it.
- R10: Static constraints. No name of the instance's vendor or of the bridge module appears in any
  file under `core/`; the runtime dependency set stays exactly `aiohttp` and `PyYAML`; the bridge's
  WebSocket client is written on `aiohttp`; the new manifest ships as package data; every new
  operator-facing document exists as `X.md` and `X.fr.md`, the English one marked normative and the
  French one saying the English text wins.

## Acceptance criteria

- AC1 (R1): With the bridge not enabled, each of the 4 shipped profiles (`config.yaml.example`,
  `config.server.yaml.example`, `agent.yaml.example`, `presence.yaml.example`) passes
  `--check-config` with the same exit status (0) and the same diagnostics output as before this
  phase, and the suite's pre-existing tests pass except the allowlisted ones.
- AC2 (R1): `git diff` of this phase shows 0 changed lines in the 4 shipped `*.example` profiles,
  and none of them contains the bridge's module name or an `inputs:` key.
- AC3 (R1): A profile enabling the bridge with `url` pointing at a loopback port where nothing
  listens reaches readiness within its startup budget; the bridge has exactly 1 `module.degraded`
  record with reason `unreachable`; a granted run call ends `refused provider_not_ready` and the
  bridge's `users.read` ends `refused provider_not_ready`, each with 0 frames sent.
- AC4 (R1): Against the fake instance, a wrong password yields readiness of the companion and the
  bridge's degraded reason `authentication_failed`; an instance requiring authentication with no
  password configured yields `authentication_required`; a malformed hello frame yields
  `protocol_error`; a failing inventory request yields `inventory_unavailable` — in all 4 cases no
  password, URL or frame text appears in any trace or log.
- AC5 (R1): The bridge's settings schema has no `required` property (the switch other modules use
  to make an unavailable dependency fail startup), and with the bridge owning (`chat`, twitch) while the fake instance is stopped, a twitch
  chat event published by the `twitch` module is dropped (R3), i.e. no fallback.
- AC6 (R2): With `twitch` and the bridge enabled and no `inputs` block, (`chat`, twitch) resolves
  to `twitch` by default rule, the bridge owns no chat pair, and (`cues`, `*`) resolves to the
  bridge.
- AC7 (R2): Two test modules both declaring (`chat`, `x`) with `default: true`, both enabled, no
  `inputs` entry: `--check-config` exits non-zero with one diagnostic naming the family `chat`, the
  platform `x` and both module names; 0 modules are prepared and 0 transports opened.
- AC8 (R2): `inputs: {chat: {twitch: [twitch, <bridge>]}}` is refused with one diagnostic naming
  `chat`, `twitch` and both modules; `inputs: {chat: {twitch: <module not enabled>}}`, an owner that
  does not declare the family for that platform, and `inputs: {weather: {...}}` (family outside
  the vocabulary) are each refused with a diagnostic naming family, platform and value.
- AC9 (R2): `inputs: {chat: {twitch: <bridge>}}` with `twitch` enabled resolves (`chat`, twitch) to
  the bridge (explicit); the `twitch` module's context view contains 0 owned pairs and the bridge's
  view contains (`chat`, twitch); `inputs: {cues: {'*': none}}` leaves every cue pair unowned.
- AC10 (R2): The configuration UI's core-settings page, rendered without opening a socket for the
  AC9 profile, shows a read-only row for (`chat`, twitch) with owner = the bridge, origin
  "explicit" and candidates {twitch, bridge}, and Check on the AC7 configuration shows the AC7
  diagnostic.
- AC11 (R2, R10): A grep of `core/` for the bridge module name and for the vendor name (any case)
  returns 0 matches, and the ownership code in `core/` contains no platform name literal.
- AC12 (R3): With (`chat`, twitch) owned by the bridge, the `twitch` module's ingestion entry
  points open 0 EventSub subscriptions, while its `chat.write` is bound and ready and a granted
  `chat.write` reaches its provider.
- AC13 (R3): A test module that publishes 3 `channel.chat.message` events for a pair it does not
  own: 0 reach any subscriber (trigger engine, `users`, `chat_context`, brain), the
  `input_ownership_rejections` counter equals 3, and exactly 1 ownership diagnostic is emitted.
- AC44 (R3, R1): A test module with no `input_families` key that publishes 3
  `channel.chat.message` events for (`chat`, x): with no module declaring (`chat`, x) and no
  `inputs` block, all 3 reach the trigger engine and the counter stays 0; with a second enabled
  module declaring (`chat`, x) `default: true`, 0 reach any subscriber and the counter equals 3;
  with `inputs: {chat: {x: none}}`, 0 reach any subscriber and the counter equals 3. Each of the 4
  shipped profiles, without the bridge, resolves no owner for any pair published by an undeclared
  shipped module (0 rejections over the existing integration tests).
- AC14 (R3): With `users` and the bridge enabled and `inputs: {viewers: {twitch: <bridge>}}`,
  preparation succeeds with no `AmbiguousBindingError`; `users.read` at a twitch destination
  reaches the bridge's provider and at a kick destination reaches the `users` provider (1
  invocation each, 0 of the other).
- AC15 (R4): The run action's discovered spec has `nature: write`, `idempotency: none`,
  `required_permissions: [automation.run]`, one destination `{platform: *, channel_id: *, scope:
  automation}`, `delivery: {text_argument: none}`, `model_proposable` false, an argument schema
  accepting `{action: "a"}` and refusing a missing `action`, an extra key, and an `action` of 0 or
  129 characters; and its description contains the sentence constant the tests define stating that
  acceptance is not confirmation.
- AC16 (R4): Against the fake instance, each of the 10 rows of the R4 table is produced by one
  test, which asserts status, code and cause exactly, `retryable: false`, and the number of request
  frames the fake server received (0 for the first 6 rows and for "before the frame left", 1
  otherwise); no test of the run action observes `status: success`.
- AC17 (R4): A call whose acknowledgement arrives after the deadline yields `external_unknown`
  cause `acknowledgement_lost`, the executor does not re-invoke the provider, and the fake server
  receives exactly 1 request frame for that `call_id`.
- AC18 (R5): With `allowlist: [{action: "A"}]` and the fake inventory {A, B}, a run of `B` is
  `refused not_allowlisted` with 0 request frames; with an empty allowlist every run is refused
  `not_allowlisted`; with `allowlist: [{group: "G"}]` and inventory {A in G, B not in G}, `A` is
  reachable and `B` refused.
- AC19 (R5): An entry with `arguments: {type: object, properties: {n: {type: integer}},
  additionalProperties: false}` refuses `{n: "x"}` and `{m: 1}` as `error invalid_arguments` with 0
  frames; an entry without `arguments` refuses `{x: 1}` and accepts `{}`.
- AC45 (R5): A settings value with two `{action: "A"}` entries, or two `{group: "G"}` entries, is
  refused by the validator with a per-field diagnostic. With entries `{action: "A", arguments:
  S1}` and `{group: "G", arguments: S2}`, A in G, S1 accepting `{n: 1}` and S2 refusing it, a run
  of `A` with `{n: 1}` sends 1 request frame; with entries `{group: "G", arguments: S1}` and
  `{group: "H", arguments: S2}`, A in G and H, the same run ends `error invalid_arguments` with 0
  frames, and a run with arguments both schemas accept sends 1 frame.
- AC20 (R5): With allowlist {A, C, D, E} and inventory {A, two actions both named "C", D
  disabled} (E absent), the connection emits exactly 1 reconciliation diagnostic naming the
  unmatched entries `C`, `D` and `E` (and not `A`); runs of `C`, `D` and `E` end `refused
  instance_action_ambiguous`, `refused instance_action_disabled` and `refused
  instance_action_missing` respectively, all with 0 request frames; a run of `A` sends exactly 1
  request frame.
- AC21 (R5): A principal without an `actions:` rule granting `automation.run` is `refused
  not_authorized` by the executor, 0 provider invocations, even for an allowlisted name.
- AC22 (R6): The bridge's settings validator refuses `ws://192.0.2.1:9/` (non-loopback, no TLS)
  and accepts `ws://127.0.0.1:9/` and `wss://192.0.2.1:9/`; a missing `url` and a non-positive or
  non-finite deadline are refused, each with one value-free diagnostic naming its field.
- AC23 (R6): Every node of the bridge's settings schema carries a non-empty `title`
  (`tests/test_manifest_presentation.py` passes for 18 manifests), `password` is in `credentials`,
  and a trace sweep after a run of every AC16 scenario finds the configured password value 0
  times.
- AC24 (R6): The fake instance requiring authentication receives an authentication response equal
  to `base64(SHA256(base64(SHA256(password + salt)) + challenge))` for the test's salt, challenge
  and password; the bridge becomes ready only after the instance accepts it and the inventory is
  read.
- AC25 (R6): Closing the fake server's connection while ready produces 1 `module.degraded` at
  once; a reconnection after the configured minimum backoff on an injected clock produces 1
  `module.ready`; the observed delays never exceed the configured maximum.
- AC26 (R6): With a queue bound of 4 and 10 events delivered before the consumer runs, 4 are
  published, the bridge's dropped-event count equals 6, and the fake server's connection is not
  stalled (a request sent afterwards is acknowledged).
- AC27 (R6): With the bridge owning only (`cues`, `*`), its subscription request names only the
  custom-event category; owning (`chat`, twitch) adds only the twitch chat and notice categories.
- AC28 (R6): Cancelling the bridge's task propagates `CancelledError`; shutdown closes the
  connection within the close budget (fake server observes the close); a run in flight after its
  frame was sent ends `external_unknown` cause `acknowledgement_lost`.
- AC29 (R7): A valid cue envelope for platform `twitch` produces exactly 1 normalised event with
  `type: channel.chat.message`, `metadata.schema_version: 2`, `metadata.source` = the bridge,
  `payload.kind: cue`, `payload.message_id` = `cue_id`; the trigger engine evaluates it with the
  policy under `triggers.<bridge>` and the bridge's default policy accepts it.
- AC30 (R7): A cue without `user_id` has an author id identical across 2 cues of one instance and
  different from every configured companion identity; a cue with 501 code points of text, with an
  empty `cue_id`, or without `platform` publishes 0 events and increments the dropped count by 1
  each.
- AC31 (R7): A brain route `match: {kinds: [cue], command: "!brb"}` with a fixed delivery
  `{action: automation.action.run, text_argument: none, arguments: {action: "BRB"}}` passes the
  brain's route-safety check at prepare; the same route with `kinds: [message]` and no audience is
  still refused, as today.
- AC32 (R7): Owning (`chat`, twitch), the bridge maps the documented twitch chat-message fixture to
  one event whose payload keys and kinds equal those the `twitch` module produces for the
  equivalent message; with `trust_instance_roles: false` an `audience` rule requiring a moderator is
  not satisfied by the fixture's moderator flag, and with `true` it is.
- AC33 (R7): A profile where the bridge owns (`chat`, twitch) without a companion identity for
  twitch fails `--check-config` with a diagnostic naming the platform; with the identity set, an
  instance chat event authored by that identity is rejected by the trigger engine's anti-echo
  invariant.
- AC43 (R7): For each platform P in {twitch, kick, youtube}, owning (`chat`, P), the bridge maps
  P's documented chat-message fixture (3 fixtures in total, one per platform) and every documented
  notice fixture of P (one per notice kind the sources ledger documents for P) each to exactly 1
  event whose `payload.kind` and payload key set equal those P's native module produces for the
  equivalent input (the comparison fixture of the native module, per kind); a notice of a kind with
  no documented mapping for P produces 0 events and increments the drop counter by 1; owning only
  (`chat`, twitch), the kick and youtube fixtures produce 0 events.
- AC34 (R7): Owning (`viewers`, twitch), the bridge's `users.read` for the instance broadcaster's
  channel returns a page validating against the shared result schema with `coverage.kind:
  active_viewers`, `coverage.complete: false` and at most `limit` users; for another channel it is
  `refused unsupported_destination`; the `users` module's pages still report `observed_authors`.
- AC35 (R8): A guard test loads every fixture and fails if any frame lacks a provenance, if a
  `documented` one names an id absent from the sources ledger, or if a fixture naming a trial
  question number (1–8) is not `observed`; at this phase's merge, with no trial record, the count of
  fixtures naming a question number is 0.
- AC36 (R8): `pytest tests/test_phase5_trials.py` with no trial variable set collects 8 tests, all
  skipped, each skip reason naming its variables, and opens 0 sockets.
- AC37 (R8): While the trial record does not exist, a docs test finds
  in `README.md` and `docs/README.md` the bridge's status "not delivered" and finds the word
  "delivered" applied to the bridge nowhere else; once the record exists, the same test requires it
  to carry an instance version, a date and one answer for each of the 8 question numbers.
- AC38 (R8): Before the phase is merged, `docs/campaigns/phase5/` contains the tracked full-branch
  gate report.
- AC39 (R9): A test extracts the 12 fragments of the guide, applies each to a copy of the
  stated shipped profile with test `${NAME}` values, and asserts `--check-config` exits 0 for
  combinations 1–8 and non-zero with the documented diagnostic for 9–12; no transport opens.
- AC40 (R9): The guide's combination table has 12 rows, each with non-empty "enabled", "disabled"
  and "refused" cells, and combination 8 states that families and cues do not reach the brain in
  that placement.
- AC41 (R10): `pyproject.toml` declares exactly the runtime dependencies `aiohttp` and `PyYAML` and
  the package-data line for the bridge manifest; the installed-distribution test discovers 18
  manifests.
- AC42 (R10): Both files of the guide exist, the English one marks itself normative, the French one states that the English text wins, and
  `tests/test_docs_bilingual.py` passes with both listed.

## Expected gains

| Gain (after this phase, impossible today) | Quantity | Cost | Criteria |
| --- | --- | --- | --- |
| Reach the instance's effect catalog (OBS, Stream Deck, Wave Link, VTube Studio, sounds, clips, rewards…) | 444 sub-action types through 1 declared action, restricted to N operator-allowlisted names/groups (N = 0 by default) | 1 module, 1 action, 1 permission | AC15, AC18, AC20 |
| Run the brain on any instance trigger the operator routes | 444 trigger types routable as cues; 1 trigger input, policies as for chat | 1 event kind, 1 family | AC29–AC31 |
| Read a platform's chat without platform credentials on the companion | 3 platforms (twitch, youtube, kick) readable with 1 instance password instead of the platform's 4–5 credentials | 1 mapping per documented event shape | AC32, AC33, AC43 |
| Viewer presence from the instance | active-viewer pages instead of authors-who-spoke | 1 enum value added to `users.read` | AC34 |
| Deterministic input ownership for every module, bridge or not | duplicate ingestion of one (family, platform) pair: from possible to refused at preparation (0 duplicate runs to absorb) | 1 manifest key, 1 profile block, 1 counter | AC7–AC9, AC13 |
| Honest silence | 0 run-action outcomes reported as success; 100 % of acknowledged runs reported `external_unknown execution_unconfirmed` | no confirmation (A6) | AC16, AC17 |
| What it costs | 0 new runtime dependencies; 0 shipped-profile changes; 5 core files touched additively; 7 existing tests adapted (allowlist); 1 module page more in the UI; a real-instance trial on Windows before "delivered" | — | AC1, AC2, AC36–AC41 |

## Test failure allowlist

- `tests/test_contracts.py::test_event_kind_vocabulary_is_the_nine_names_in_order` — asserts the 9-name vocabulary; R7 (A5) adds `cue` as a tenth kind.
- `tests/test_contracts.py::test_platform_notice_kinds_exclude_message_and_watch_tick` — asserts the notice kinds equal every kind but `message` and `watch_tick`; R7 adds `cue`, which is not a notice (the 7 notice kinds are unchanged).
- `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` — asserts exactly 17 manifests and the exact top-level key set per manifest; R2 adds `input_families` to `twitch`, `kick`, `youtube`, `users`, and R4–R7 add the 18th manifest.
- `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest` — asserts 17 shipped manifests; R10 ships an 18th.
- `tests/test_profiles.py::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency` — asserts 17 manifests and 17 package-data lines; R10 adds the bridge's line (dependencies unchanged).
- `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` — asserts 17 discovered manifests; R10 ships 18.
- `tests/test_users.py::test_manifest_is_v2_and_declares_users_read_without_granting_it` — asserts the coverage.kind enum is exactly `["observed_authors"]`; R7 widens it to include `active_viewers` (the module's own pages still report `observed_authors`).

Search method: grep of `tests/` for `== 17`, `EVENT_KINDS ==`, `PLATFORM_NOTICE_KINDS`,
`observed_authors` and `DECLARATION_KEYS`. Blind spot: a test asserting an exact
`DIAGNOSTIC_COUNTERS` tuple or an exact core-settings page section list is not detected by this
search; the plan must re-run the suite after the R2/R3 core changes and extend this list only with
tests whose assertion R2 or R3 contradicts.

## Caller Enumeration

No existing public function signature changes. The changes are additive: an optional manifest key
(`input_families`), an optional top-level profile key (`inputs`), a read-only ownership view on
the per-module runtime context, a new event kind, a widened enum in one action's result schema,
and a new module. Callers and consumers whose behaviour is affected:

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/loader.py | `_validate_manifest` / `_declaration` | Accept and validate the optional `input_families` key; manifests without it are undeclared publishers, delivered as today for pairs with no resolved owner (R3 compatibility rule). |
| core/loader.py | `ModuleLoader.activate_enabled` | Resolve ownership before preparing any module; refusal raised before the first `prepare`. |
| core/main.py | `load_config`, `check_config`, `run`, `main` (`--check-config`) | Accept/validate the optional `inputs` block; same signatures. |
| core/runtime.py | `RuntimeContext.for_module` → `ModuleContext` | Gains a read-only owned-pairs view; existing properties unchanged, `RUNTIME_API` unchanged. |
| core/triggers.py | `_normalize` (via `validate_event_kind`), event_kind parameter schema (enum from `EVENT_KINDS`) | Accept `cue` automatically through the shared vocabulary; no code change required. |
| modules/brain/__init__.py | route validation (`kinds` ⊆ `EVENT_KIND_SET`), route-safety rule (`PLATFORM_NOTICE_KINDS`), empty-text rule | Accept `cue` in routes; treat it like a notice for route safety and empty text. |
| modules/kick/__init__.py, modules/youtube/__init__.py | module-local `EVENT_KINDS` mappings | Unaffected (module-local names); ingestion gated by the owned view. |
| modules/twitch/__init__.py | ingestion start | Gated by the owned view; `chat.write` unchanged. |
| modules/users/__init__.py | `users.read` provider binding, chat subscription | Bind only owned platforms; skip `cue` authors. |
| modules/proxy/__init__.py | actions allowlist spec lookup | Unchanged; the bridge's run action is found in the discovered/disabled catalog like `screen.capture`. |
| core/config_ui/__init__.py | core-settings page renderer, Check | Add the read-only ownership table and ownership diagnostics. |

Search method: grep of `core/` and `modules/` for `EVENT_KINDS`, `EVENT_KIND_SET`,
`validate_event_kind`, `PLATFORM_NOTICE_KINDS`, `for_module`, `ModuleContext(`, `users.read`,
`channel.chat.message`. Blind spot: dynamic attribute access on the context is not detected.
