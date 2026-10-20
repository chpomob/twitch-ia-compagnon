---
name: "phase8-declared-triggers"
version: "1.0"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec, phase8, triggers, channel-points, operator, wake-word, timer]
targets:
  - file: core/contracts.py
    description: "Append `reward`, `manual`, `speech`, `timer` to EVENT_KINDS (indices 10–13), keep PLATFORM_NOTICE_KINDS and CHAT_FAMILY_KINDS exactly as they are, and let a TriggerTypeDeclaration carry the declared-type fields (kinds, match rule, optional probability parameter, hook flag and bound) with their structural validation."
  - file: core/triggers.py
    description: "TriggerRegistry accepts a declared type instead of refusing every non-generic name; the engine evaluates declared match rules over the payload, draws for a declared probability parameter, invokes a bounded fail-closed hook for a hooked type, and emits the new bounded reason tokens; the four generic types and every invariant stay as they are."
  - file: core/loader.py
    description: "Parse and validate the new manifest keys (`triggers.fields`, and per type `kinds`, `match`, `probability_parameter`, `hook`, `hook_timeout_seconds`) with diagnostics naming the input and the field."
  - file: core/runtime.py
    description: "Give an activated module the means to supply the hook of a type its own manifest declares as hooked, and nothing for any other module's type."
  - file: modules/twitch/module.yaml
    description: "Declare the `reward` trigger type and the `reward` payload fields, add `reward` to the `event_kind` enum and to the `notices.kinds` enum; default policy unchanged."
  - file: modules/twitch/__init__.py
    description: "Subscribe to `channel.channel_points_custom_reward_redemption.add` iff `notices.kinds` lists `reward`, normalize a redemption into a `reward` event, degrade only the `reward` capability on refusal."
  - file: modules/brain/module.yaml
    description: "Append the four new kinds to the route `match.kinds` enum so a route can name them."
  - file: modules/operator/module.yaml
    description: "New manifest v2: settings (buttons, destination, surfaces, listen address, token credential, cooldown, overlay layout), the `input` lifecycle role, the `button` declared type and the `event_kind` generic narrowed to `manual`."
  - file: modules/operator/__init__.py
    description: "New input: token-gated loopback page, overlay page and press endpoint (the endpoint the press command calls), one `manual` event per accepted press, decided and admitted like any input; no module-level import of `core.runtime`, `core.loader` or `core.lifecycle`."
  - file: modules/operator/__main__.py
    description: "New: the press command `python -m modules.operator press <button> [--profile PATH]` (A8): one POST to the running input's press endpoint with surface `hotkey`, exit codes 0/2/3/64, no token printed, no runtime imported, no key bound."
  - file: modules/timer/module.yaml
    description: "New manifest v2: settings (channels with destination and prompt text), the `input` role, the `interval` declared type with its probability parameter and window."
  - file: modules/timer/__init__.py
    description: "New input: one `timer` event per configured interval and channel on the injected clock, decided and admitted like any input."
  - file: modules/audio_input/module.yaml
    description: "Declare `produces: [channel.chat.message]`, the `input` role, the `listen` and `wake_word` settings, the `wake_word` payload fields, the generic `keyword`/`event_kind` types and the hooked `wake_word` type with its default policy."
  - file: modules/audio_input/__init__.py
    description: "Opt-in listening in one order — detector verdict (command or HTTP), a candidate decided on that verdict, then, only on acceptance, acquisition with a bounded pre-roll and one transcription into a `speech` event — started in `start_inputs` and stopped in `stop_inputs`, with the bounded restart backoff and the detector's supervision; the wake-word hook over the candidate's verdict; `audio.capture` unchanged."
  - file: pyproject.toml
    description: "Add the `modules.operator` and `modules.timer` package-data lines; runtime dependencies stay exactly aiohttp and PyYAML."
  - file: tests/test_declared_triggers.py
    description: "New: manifest validation, matcher semantics, narrowing, probability draw counts, hook bounds and fail-closed outcomes, with fixture modules only."
  - file: tests/test_operator.py
    description: "New: socket-free request-core tests of the three surfaces, token, origin, cooldown, unknown button, event shape and admission; the press command over a fake opener."
  - file: tests/test_timer.py
    description: "New: ManualClock tests of cadence, window, probability and startup refusals."
  - file: tests/test_audio_input.py
    description: "Extend: listening off by default, verdict → candidate → acquisition and transcription only on acceptance → speech event, declined candidates acquiring nothing, transcription failure, wake-word detector outcomes and bounds, audio never stored."
  - file: tests/test_twitch.py
    description: "Extend: reward subscription gating, normalization, malformed redemptions, refusal degrading `reward` only, the `reward` type."
  - file: tests/test_contracts.py
    description: "Adapt the vocabulary assertion to fourteen kinds (allowlisted)."
  - file: tests/test_input_ownership.py
    description: "Adapt the last-kind assertion (allowlisted)."
  - file: tests/test_examples.py
    description: "Adapt module names, manifest count 19→21, twitch trigger type list, audio_input declaration keys (allowlisted)."
  - file: tests/test_manifest_presentation.py
    description: "Adapt shipped-manifest count 19→21 (allowlisted)."
  - file: tests/test_profiles.py
    description: "Adapt discovered-manifest and package-data counts 19→21 (allowlisted)."
  - file: tests/test_config_ui.py
    description: "Adapt shipped-module count 19→21 (allowlisted)."
  - file: tests/test_docs_bilingual.py
    description: "Add the `triggers` guide pair (allowlisted)."
  - file: docs/triggers.md
    description: "New normative operator guide: generic and declared types, the match vocabulary, each of the four new trigger families with a configuration example, the manual real-instance trial procedure, and continuous listening documented as out of scope."
  - file: docs/triggers.fr.md
    description: "New French translation, stating that the English text wins."
  - file: README.md
    description: "Capability list and layout table gain `operator`, `timer`, listening and channel points, each stated as proven against fakes only."
  - file: docs/campaigns/phase8/summary.md
    description: "New phase summary: per requirement the commit and the test that carries it, what can and still cannot be triggered."
  - file: docs/campaigns/phase8/trial-record.md
    description: "New record template of the manual real-instance trial, status `not run` until an operator fills it."
---

# Phase 8 — triggers a module can declare (the kernel stops enumerating them)

## Problem

The product's own decision says every input declares the trigger types it supports and that the
trigger decides, before admission and budgets, whether an ingested event becomes a run. The code is
narrower: `core/triggers.py` holds `BUILTIN_TRIGGER_TYPES`, a closed set of four evaluators
(`probability`, `audience`, `keyword`, `event_kind`); a manifest may only declare a subset, and
`TriggerRegistry.register` refuses any other name with "is not a trigger type this runtime can
evaluate (...); supported: audience, event_kind, keyword, probability".

So each of the four triggers the operator asked for is a kernel change today, and three cannot be
expressed at all: channel points (no subscription, no kind), the operator's own button (no surface, no
kind — only a Streamer.bot `cue` when Streamer.bot exists), the streamer's spoken keyword
(`audio_input` is an action the brain calls, never an input), and a random trigger from time to time
(only as the accident `watch_tick` + `probability`).

This phase moves the trigger types to the inputs, as decided, and builds the four families on top.

### Binding decisions (from the brief; this spec must not narrow them)

1. A module may declare trigger types (name, parameter schema, declarative match rule over the payload
   using only `field`, `equals`, `in`, `gte`/`lte`, `matches`, `all`, `any`, `not`); the kernel
   evaluates them, keeps the four generic types, and no code runs in the hot path for a declared type.
2. Where a rule cannot be declared, a bounded hook (`matches` callable), called only on the declaring
   module's own events, under a hard timeout, outcome recorded; raise or timeout → no trigger; it
   returns a candidacy, never a run.
3. A declared type narrows, never widens; configuration is still validated against the module's
   declared schema and the kernel's canonical one; admission, budgets and DEFAULT-DENY are untouched.
4. Channel points: subscribe to `channel.channel_points_custom_reward_redemption.add`, new kind
   `reward`, a `reward` type keyed on reward identifier with an optional cost range; the vocabulary
   grows by appending.
5. A native `operator` input publishing `manual`: token-gated local page, global hotkey, button inside
   an image (overlay browser source); one event, one button identifier; surfaces are configuration;
   Streamer.bot stays optional and `cue` unchanged.
6. `audio_input` can publish what it hears (transcribed text, who spoke) so `keyword` works on speech;
   the wake word is the audio input's own hooked type with the detector configured
   (`{kind: command, argv}` or HTTP), never embedded; no new core dependency; continuous audio and
   always-on transcription out of scope.
7. A `timer` input declares an `interval` type (every N minutes, probability P, optional window).

Forbidden: a new core runtime dependency (only `aiohttp`, `PyYAML`); any bypass of admission, budgets
or action authorization; requiring Streamer.bot; continuous listening on by default or recording audio
no run asked for; any behaviour change for a profile enabling none of this.

### Assumptions (documented; each extends, none narrows, a decision)

- **A1 — Two kinds beyond `reward` and `manual`.** The brief names `reward` and `manual` as the kinds
  appended. A spoken utterance and a timer firing also need a kind: as `message`, a spoken event on a
  Twitch destination would belong to the `chat` input family, which `audio_input` does not own, and the
  ownership guard (`core/runtime.py`, `refused_pair`) would refuse it; and a kind names *what happened*,
  so neither is a chat message nor a `watch_tick`. The vocabulary therefore grows by appending, in this
  order, `reward`, `manual`, `speech`, `timer`. None of the four belongs to an input family (like
  `watch_tick`), and none is a platform notice, so `PLATFORM_NOTICE_KINDS` and `CHAT_FAMILY_KINDS` are
  unchanged and the brain's route-safety classification counts all four as viewer-reachable — the
  conservative reading; making `manual`/`timer` privileged is left to a later phase.
- **A2 — `matches` is a wildcard, not a regular expression.** `matches` compares a string with a
  case-insensitive pattern where `*` is any run of characters and `?` one character, at most 128
  characters long. A regular expression from configuration evaluated on viewer text in the hot path
  is a denial-of-service surface; a wildcard is evaluated in at most (len(text) + 1) × (len(pattern) +
  1) steps, a step being one comparison of a pattern position with a text position (including each
  resumption after a `*`), counted by the matcher and exposed in the engine's counts as
  `wildcard_steps` (AC36).
- **A3 — A declared type may carry one probability parameter.** "Every N minutes with a probability P"
  (decision 7) needs a draw, and the closed operator list has no random operator. A declared type may
  therefore name one of its own parameters as its probability (number in [0, 1]); the kernel evaluates
  it with the same injected random source and the same exactly-one-draw rule as the generic
  `probability`. No operator is added to the vocabulary.
- **A4 — Inputs declare the payload fields they emit.** "It cannot assert a field the input does not
  produce" (decision 3) is checked at load against a field catalog the input declares
  (`triggers.fields`), plus the canonical normalized fields every input emits.
- **A5 — Listening is opt-in, detection by detection, and keeps no audio.** "Continuous audio and
  always-on transcription" (out of scope) is read as transcribing on a schedule or continuously,
  streaming audio continuously to a transcriber or to the core, or any listening enabled by default;
  all three are out of scope. In scope: with `listen.enabled: true` (default `false`) the audio input
  invokes a configured detector that is the cheap wait — it waits, outside the core, until it detects
  and reports a verdict only (detected, the phrase) — decides a candidate carrying that verdict, and
  only for an accepted candidate acquires one bounded segment (with a bounded pre-roll) and
  transcribes it, so the cost is bounded by the wake word, not by time (R7). Each segment is
  transcribed once, held in memory only until it is transcribed, and never written to the attachment
  store or to disk by listening. Audio is stored only when a run calls
  `audio.capture`, exactly as today.
- **A6 — Reserved author identities.** The three new inputs have no platform viewer. Their events
  carry reserved author identities containing `:` (`operator:<button>`, `timer:<channel>`,
  `voice:<speaker>`), which no platform viewer can hold (platform inputs already refuse such ids) and
  which the brain already excludes from viewer memory. Each event also carries a configured
  destination `{platform, channel_id}`: the channel its run answers on.
- **A7 — The operator token is configured, not printed per run.** The configuration UI prints a
  per-process token; an OBS browser-source URL must survive restarts, so the operator input's token is
  a configured credential (`${...}`), gated otherwise exactly like the configuration UI (loopback
  bind by default, accepted Host, token, Origin check on state-changing requests).
- **A8 — The hotkey surface is a shipped command.** A global hotkey needs OS APIs that the standard
  library does not offer and the core may not add. The module ships `modules/operator/__main__.py`,
  run as `python -m modules.operator press <button> [--profile PATH]` (default `config.yaml`). It
  reads the profile with PyYAML, takes the operator's listen address and resolves only the token's
  `${NAME}` reference from the environment, sends one POST to the running input's press endpoint
  naming the button with surface `hotkey` (standard-library HTTP client, 5 s timeout), prints no
  token, and exits 0 when the input accepted the press, 2 when the input refused it (unknown button,
  cooldown, token, surface disabled), 3 when the input could not be reached or did not answer within
  5 s, and 64 on a usage or profile error. Running it imports nothing from `core.runtime`,
  `core.loader` or `core.lifecycle` (so `modules/operator/__init__.py` keeps them out of its
  module-level imports), starts no runtime, opens no listener and binds no key: binding the command
  to a global key is an OS/tool step the guide documents.

## Requirements

Non-trivial requirement count: 8 (R1–R8). R9 is documentation and evidence and is trivial under the
classification (no branching, no boundary, no unverifiable invariant).

- R1: **A module declares trigger types, and the registry accepts them at load.** A manifest's
  `triggers.types[i]` is either a generic type (one of the four names, narrowing its canonical schema
  as today) or a declared type. A declared type carries: a `name` matching `[a-z][a-z0-9_]*` of at most
  32 characters and distinct from the four generic names; a `parameter_schema` in the existing schema
  subset; a non-empty `kinds` list, each in `EVENT_KINDS`; and exactly one of a `match` rule or
  `hook: true` (R3). A `match` rule is a tree of the closed node set `all`, `any`, `not`, `equals`,
  `in`, `gte`, `lte`, `matches`, whose leaves are operands: `{field: <path>}`, `{param: <name>}`, or
  a literal (string, number, boolean, or list of at most 64 such scalars); depth at most 8, at most 64
  nodes. A `field` path names a key path under the event payload and must be a canonical normalized
  field (`kind`, `text`, `platform`, `channel_id`, `message_id`, `author.id`, `author.display_name`)
  or one the input declares in `triggers.fields` (path → scalar type). A `param` names a property of
  the type's own `parameter_schema`, or a dot-separated path into a property whose schema is an object
  (e.g. `window.start_minute`), each segment a declared property; a `param` operand is absent when any
  segment of its path is absent from the configured parameters, and a parameter that is not required
  (or that lies under a non-required object) may not be referenced under `not`. An optional
  `probability_parameter` names a top-level parameter that the schema lists as required and whose
  schema is a number bounded within [0, 1]; naming a non-required parameter is refused at load.
  A type that is neither generic nor declares a `match` or a hook is refused with a diagnostic at
  `triggers.<input>.types[i].name`. Every other violation is refused at load, before any transport is
  opened, with a diagnostic naming the input and the offending field path and echoing no configured
  value. Configured policies are validated against the declared parameter schema and against these
  kernel checks, exactly where generic policies are validated today; the "is not a trigger type this
  runtime can evaluate" refusal no longer applies to a well-formed declared type.

- R2: **A declared type decides candidacy only, and only narrows.** For an event, the existing
  invariants (internal-trace exclusion, schema-version-2 normalization, authenticity, anti-echo) and
  the dedup window run before any declared rule, unchanged. A declared rule is evaluated only for an
  event whose `metadata.source` is the input that declared it (a policy naming the type for another
  input is refused by the existing validation). Its outcome: an event whose kind is not in the type's
  `kinds` does not match; otherwise the `match` tree is evaluated over the payload, where a scalar comparison
  (`equals`, `gte`, `lte`, `matches`) whose field is absent or whose value is of a different JSON type
  than its other operand is false; `in` (left operand a scalar, right operand a list) is false when the
  left value is absent or not a scalar, and otherwise is true exactly when the list holds an element of
  the same JSON type equal to it (strings compared case-sensitively); a comparison whose `param`
  operand is absent from the configured parameters is true; `equals` compares strings
  case-sensitively; and `matches` follows A2;
  when a `probability_parameter` is declared, exactly one value is drawn from the engine's random
  source per evaluation of the rule, whether or not the tree matched, and the rule matches only when
  the tree matched and the draw is below the configured probability. *`draws`.* TriggerDecision gains
  one new field, `draws`, and it is the only change to the decision's shape: a tuple with one entry
  per evaluated declared rule that has a `probability_parameter`, in policy order — so at most as many
  entries as the policy has rules. Each entry holds exactly five scalars: `rule` (the rule's index in
  the policy), `parameter` (the probability parameter's name), `probability` (its configured value, in
  [0, 1]), `draw` (the value as returned by the random source, in [0, 1)) and `ordinal` (the 0-based
  count of values the engine's random source had served before this draw), which with the recorded
  source reproduce the draw. Its redaction is that closed list: no payload field, no event text and no
  other parameter's value is ever an entry or part of one. `draws` is kept with the decision in the
  dedup window and on its trace record, and a dedup redelivery returns the recorded entries and draws
  nothing. A policy with no declared probability rule — every generic-only policy, including one that
  uses the generic `probability` type, whose draw is not recorded here — leaves `draws` empty (`()`),
  so the existing decision-shape tests hold unmodified (AC10). Evaluating a declared rule runs
  no module code and opens no transport. Apart from `draws`, the decision keeps its shape (`accepted`, bounded reason,
  `policy_version`, identifiers, no `run_id`); declared-rule reasons are `<type>_match`,
  `<type>_no_match`, `<type>_kind_mismatch`, `<type>_probability_miss`, prefixed `accepted:`/
  `rejected:` and bounded to 120 characters. An accepted decision is only a candidacy of that input's
  event: the event then goes through admission, budgets and action authorization exactly as a chat
  event does — for an input that decides before it builds its run's event (R7), the candidate is what
  is decided and the `speech` event built after transcription is what is admitted under that decision
  — and a policy composed only of generic types decides exactly as before this phase.

- R3: **A hooked type is bounded and fail-closed.** A declared type with `hook: true` declares
  `hook_timeout_seconds` in (0, 10]. Only the module that declared the type may supply its hook, at
  activation; an attempt by any other module is refused. The hook receives a read-only view of the
  event and the rule's configured parameters and is invoked at most once per evaluation — never for an
  event rejected by an invariant, recalled from the dedup window, of a kind outside the type's
  `kinds`, or published by another input. When an input decides before it builds the event its run
  receives (R7's candidate), the hook evaluates the candidate exactly as the input submitted it, before
  any later stage runs; an event the input builds after the decision is never submitted to the engine
  and so is never what a hook evaluates. Its outcome is: the boolean `True` → match (reason
  `<type>_match`); `False` → no match (`<type>_no_match`); any other return value or a raised
  exception → no trigger (`<type>_hook_error`); not answered within `hook_timeout_seconds` of the
  engine's clock → no trigger (`<type>_hook_timeout`), the evaluation then completing even if the hook
  never returns; no hook supplied → no trigger (`<type>_hook_unavailable`). These three failure
  outcomes are a hard veto on the whole decision: after every rule of the policy has been evaluated,
  and before and outside the policy's combination, the engine checks for a failed hook outcome and, if
  there is one, decides `rejected:` with the first failing rule's reason in policy order — whatever the
  combination (`all_of`, `any_of`, `none_of`), whatever other rules matched, and whether the policy is
  configured or the module's default. Only a `True` or `False` hook outcome ever enters the
  combination, so no combination, polarity or default can turn a failed hook into an acceptance. Every outcome is a recorded
  trigger decision like any other (counted, traceable, retained in the dedup window); a hook can
  neither create a run nor change admission, budgets or authorization, and a hook's failure never
  stops ingestion of later events.

- R4: **The vocabulary grows by appending, and nothing else moves.** `EVENT_KINDS` becomes the ten
  existing kinds in their existing order followed by `reward`, `manual`, `speech`, `timer` (14 kinds;
  indices 0–9 unchanged). `PLATFORM_NOTICE_KINDS` (7 kinds) and `CHAT_FAMILY_KINDS` are unchanged;
  the four new kinds belong to no input family (A1). The canonical `event_kind` schema and the brain's
  route `match.kinds` enum accept the four new kinds; the shipped manifests of `kick`, `youtube`,
  `streamerbot` and `watch` keep their narrowed enums. Every shipped `*.example` profile is unchanged
  and, started with fakes, makes the same trigger decisions, subscriptions and admissions as before
  this phase; no new runtime dependency is added.

- R5: **Channel points are a first-class trigger.** When `notices.kinds` lists `reward`, the Twitch
  input adds one EventSub subscription of type `channel.channel_points_custom_reward_redemption.add`
  (version 1, condition `broadcaster_user_id` = the configured broadcaster); when it does not, no such
  subscription is requested. A refused or unanswered subscription is one `module.degraded` naming the
  `reward` capability, value-free, and chat continues. A redemption notification is normalized into a
  schema-version-2 event of kind `reward`: `message_id` = the redemption `id`, `author` = the redeeming
  user's id and display name, `text` = the trimmed `user_input` when non-empty else the reward title,
  and `payload.reward` = `{id, title, cost}`; a notification missing the redemption id, the reward id
  or an integer cost, or whose user id contains `:`, publishes nothing and is counted
  `counts["invalid"]`. The manifest declares the type `reward` (kinds `[reward]`) with parameters
  `reward_ids` (required, list of strings), `min_cost` and `max_cost` (optional, integers ≥ 0): it
  matches when the reward id is listed and the cost is within the given bounds. The Twitch default
  policy is unchanged; fulfilling or refunding a redemption is out of scope.

- R6: **The operator has a native button, three surfaces, one event.** A new `operator` input
  publishes one `manual` event per accepted press: `payload.button` = `{id, surface}` with `surface`
  in `page`, `hotkey`, `overlay`; `text` = the button's configured non-empty `prompt_text`; `author` =
  `operator:<id>` with the button's label; a unique `message_id` per press; `platform`/`channel_id` =
  the configured destination. The surfaces are a local page listing the configured buttons, the
  shipped press command (A8), and an overlay page for a browser source showing an optional configured
  image with buttons placed by configured rectangles (percent coordinates), each press sent back to
  the companion. The input's listener opens in the `start_inputs` phase — after the readiness
  barrier, like every other input — and closes in `stop_inputs`; no listener is open, and no press
  produces an event, outside that span. Each surface is enabled by configuration (all disabled by default); enabling the
  module with no surface, with an empty or shorter-than-32-character token, with a button without
  `prompt_text`, or with a non-loopback bind address without the explicit non-loopback setting is a
  startup diagnostic naming the field. Every request without the exact token (constant-time compare),
  with a `Host` not accepted, or — for a press — with an `Origin` other than the page's own when one is
  sent, is refused with no event and counted; a press naming an unknown button, or arriving within the
  button's `cooldown_seconds` (default 2, range 0–3600) of its last accepted press, is refused with no
  event and counted. The token never appears in an event, trace, diagnostic, count name or access log.
  The manifest declares the type `button` (kinds `[manual]`, required `button_ids` list) and the
  generic `event_kind` narrowed to `[manual]`, whose `{kinds: [manual]}` is the default policy.
  Streamer.bot is neither required nor touched.

- R7: **The streamer's voice is an input; its wake word is a hooked type.** `audio_input` gains
  `listen` settings — `enabled` (default `false`), `source` (the name of a configured `command` source,
  the segment source of step 3 below), `pre_roll_seconds` (default 2, range 0–5, and less than
  `listen.max_seconds`), `speaker` (default `streamer`), `destination` `{platform, channel_id}`,
  `retry_seconds` (default 5, range 1–300), `max_seconds` (default 10, integer, range 1 to the
  module's `max_seconds`) — and
  `wake_word.detector` settings: `{kind: command, argv}` (given no audio by the core; exit 0 with the
  matched phrase as its standard output, trimmed, at most 64 characters = detected; exit 1 = returned
  without detecting; anything else = a detector failure) or `{kind: http, endpoint, api_key?}` (one
  request per invocation, carrying no audio; a JSON body `{"detected": true, "phrase": <string of at
  most 64 characters>}` = detected, `{"detected": false}` = returned without detecting; anything else
  = a detector failure). A detector invocation is a listener whose wait is by nature long: the
  companion sets no per-call timeout on it (no process deadline, no request timeout), and a timeout
  internal to the detector, if it has one, is the detector's own configuration, not the companion's.
  With `listen.enabled: false`, no detector or source is ever started and the
  module behaves exactly as in phase 7; with `listen.enabled: true` and no `wake_word.detector`, the
  module refuses to start with a diagnostic naming `wake_word.detector`; a `listen.max_seconds` or
  `listen.pre_roll_seconds` outside its range is refused likewise, naming the key.
  *Order — one way.* Each iteration of the loop runs these four steps in this order and never in
  another. (1) *Verdict.* The detector runs: one invocation, which waits outside the core on its own
  detector (voice activity or a wake word) while the core reads no audio, and settles as a verdict
  only — detected, and the phrase it matched; no text, no audio. A detector that returned without
  detecting, or failed, settles with no candidate. (2) *Candidate.* On a detected verdict the module
  submits a candidate to the trigger engine: a schema-version-2 event of kind `speech` whose
  `metadata.source` is this input, carrying `payload.wake_word = {detected: true, phrase}`, `text` =
  the phrase (never a transcription), `author` = `voice:<speaker>`, a unique `message_id` and the
  configured destination. The resolved policy decides it — the hooked `wake_word` type through its
  hook, or a declarative rule (the generic `keyword`, or a declared `match` over `wake_word.phrase`).
  Since nothing is transcribed before the decision, `keyword` and the `wake_word` type evaluate the
  phrase the detector reported: matching the full transcription before deciding would require
  transcribing every utterance, which this phase does not do (it is the always-on path the brief
  excludes), and a future opt-in plugin could change that without changing this contract. In decision
  6's terms, `keyword` works on speech as the wake word, not as a general speech-to-text keyword.
  The candidate is never published on the bus. (3) *Acquisition.* Only if that decision accepts, the
  module acquires the segment by invoking the named source once, with `AUDIO_INPUT_PRE_ROLL_SECONDS` =
  `listen.pre_roll_seconds` and `AUDIO_INPUT_MAX_SECONDS` = `listen.max_seconds` in its environment:
  the source exits 0 having written exactly one WAV of at most `listen.max_seconds` (its duration as
  the module reads it from the WAV header) and `max_bytes` that begins no more than
  `listen.pre_roll_seconds` before the verdict, so the phrase is not lost (keeping that bounded
  pre-roll is the source's own concern, outside the core); any other outcome — a longer WAV included —
  is a source failure, counted, and the module neither trims nor pads the segment it transcribes. The segment is then transcribed exactly once through
  the existing transcription settings. (4) *Event.* The run receives a schema-version-2 event of kind
  `speech` whose `text` is that transcription, with the candidate's `author`, `message_id` and
  destination: it is published and admitted under the candidate's decision, and is never submitted to
  the engine. A declined candidate acquires nothing and transcribes nothing, and the `speech` event is
  never the candidate a hook evaluates. *Two `speech` events, one kind.* The candidate adds no kind:
  `speech` is appended with `reward` and `manual` (R4), in the existing order and meaning, and both
  events are of that kind. The candidate is unpublished, submitted to the engine, and carries the
  detector's verdict — `payload.wake_word` and `text` = the phrase; the admitted event is published,
  never submitted to the engine, and carries the transcription — `text` = the transcription and no
  `payload.wake_word`; they share `author`, `message_id`, destination and `metadata.source`.
  A source failure, a failed transcription or an empty
  transcription publishes nothing, is counted, is not retried and is not itself the start of another
  acquisition. At most one detector invocation, one acquisition and one transcription are in flight.
  The number of acquisitions and of transcription calls therefore never exceeds the number of
  accepted candidates, itself at most the number of detections: the loop's cost is bounded by the wake
  word, not by time. Always-on transcription and continuous streaming of audio, to a transcriber or to
  the core, are out of scope.
  *Lifecycle.* Activation (`prepare`) only validates the `listen` and `wake_word` settings, resolves
  the named source and registers the loop's supervised-task owner; it starts no process, reads no
  audio and emits no input. The loop starts in the `start_inputs` phase — after the readiness barrier,
  the phase in which every other input begins ingesting — with the first detector invocation. A
  startup that never reaches `start_inputs` for this module (a refused setting, a failed `prepare` of
  any enabled module, a barrier that does not open) leaves no detector or source process started and
  no audio read. The loop stops in `stop_inputs`: no detector invocation or acquisition starts after
  it, an in-flight detector or source process is terminated and its partial output discarded, and a
  segment not yet transcribed is released untranscribed.
  *Supervision.* A detector that ends without a verdict — a `command` process that cannot start or
  exits other than 0 or 1 (a signal included) or exits 0 with a phrase over 64 characters, an HTTP
  endpoint that is unreachable, closes the connection or answers outside the two verdict shapes — is
  a detector failure and never reads as "no wake word": it submits no candidate, and the detector is
  started again after the restart backoff below, so a dead detector never spins. A detector
  terminated by `stop_inputs` is not a failure. *Health — two states, one report per transition.*
  The module is `healthy` or `degraded` with respect to its detector, starting `healthy`. In
  `healthy`, a detector failure moves it to `degraded` and reports exactly one `module.degraded` for
  `audio_input` with the value-free reason `wake_word_detector_failed`. In `degraded`, a further
  detector failure reports nothing (no storm) while the backoff below keeps growing to its cap; the
  first detector invocation that settles with a verdict — detected, or returned without detecting —
  moves it back to `healthy` and reports exactly one `module.ready` for `audio_input`, so recovery is
  reported, not silent; the next failure after that recovery reports `module.degraded` again. Source,
  transcription and empty-transcription outcomes are counted and backed off but neither degrade nor
  clear this state, since a detector verdict already precedes each of them.
  *Restart.* The next detector invocation starts only once the previous iteration is settled, on the
  injected clock: 1 s after (c) its candidate is declined or its `speech` event is published; and
  `retry_seconds × 2^(n−1)`, capped at 300 s, after the n-th consecutive unproductive settlement — a
  detector invocation that returned without detecting, a detector failure, a source failure, (a) a
  transcription error, or (b) an empty transcription — with n reset to 0 by (c). Hence at most one
  invocation starts per second, and repeated unproductive settlements back off to one per 300 s.
  Candidates are decided, and `speech` events published, in detection order. The segment's audio
  never reaches the bus, bus history, the audit stream or the attachment store and is released once
  it is transcribed (A5). The manifest declares the fields `wake_word.detected` (boolean) and
  `wake_word.phrase` (string) in `triggers.fields`, the generic `keyword` and `event_kind` (narrowed
  to `[speech]`) and the hooked type `wake_word` (kinds `[speech]`, no parameters,
  `hook_timeout_seconds: 2`), whose hook reads only the candidate's verdict — `True` when
  `payload.wake_word.detected` is `true`, `False` when it is `false`, and it raises on any other
  shape — and invokes no detector and reads no audio. `wake_word` is the default policy, so a candidate
  decided while the module has supplied no hook yields `wake_word_hook_unavailable` and no run.
  `audio.capture` is unchanged; the core gains no dependency.

- R8: **A timed random trigger is a setting.** A new `timer` input declares the type `interval`
  (kinds `[timer]`) with parameters `every_minutes` (required integer 1–1440), `probability` (required
  number 0–1, its `probability_parameter`) and an optional `window` `{start_minute, end_minute}`
  (integers 0–1439, start < end, host local time, both bounds inclusive); `interval` is a declared
  `match` rule (no hook) over the declared field `timer.minute_of_day` (integer) whose window bounds are
  the `param` paths `window.start_minute` and `window.end_minute`, so an absent window always matches
  (R1, R2); its default policy is
  `interval{every_minutes: 30, probability: 0.25}`. For each configured channel (destination and
  non-empty `prompt_text`), the input publishes one `timer` event every `every_minutes` of the
  injected clock since its `start_inputs` phase (after the readiness barrier, like every other input), read from the policy resolved for that channel, with
  `text` = that channel's configured `prompt_text`, `payload.timer` = `{sequence, minute_of_day}`,
  `author` `timer:<channel_id>`, and `message_id`
  `timer:<platform>:<channel_id>:<sequence>`; the event is accepted only when `minute_of_day` is
  within the window (when given) and the one draw is below `probability`. A configured channel whose
  resolved policy does not hold exactly one `interval` rule is a startup diagnostic naming the input
  and the field. No timer event is published before `start_inputs` or after `stop_inputs`.

- R9: **Documented, summarized, and demonstrable from fakes.** `docs/triggers.md` (with
  `docs/triggers.fr.md`) documents the generic and declared types, the match vocabulary with A2–A4, one
  configuration example per family (reward, button, spoken keyword and wake word — both over the
  detector's phrase —, interval), that a
  configured policy replaces the default entirely, that continuous listening is a future, deliberately
  expensive plugin, and the manual real-instance trial whose record is
  `docs/campaigns/phase8/trial-record.md` (status `not run` until an operator runs it). The README and
  `docs/campaigns/phase8/summary.md` state per family what is proven against fakes only. No test opens
  a socket, contacts Twitch, spawns a real recorder or reads a microphone.

## Acceptance criteria

Numbering is append-only: AC34–AC40 were added after AC33 by revision and belong to R3 (AC34), R2
(AC35, AC36), R7 (AC37–AC39) and R6 (AC40) — each criterion's tag names its requirement — and no
criterion is renumbered to sit beside its requirement.

- AC1 (R1): A fixture manifest declaring a type `badge_tier` with `kinds: [message]`, a
  `parameter_schema` with required `tiers` (array of string) and the rule
  `{in: [{field: badge.tier}, {param: tiers}]}`, and `triggers.fields: {badge.tier: string}`,
  registers without error, and a configured policy `badge_tier{tiers: ["gold"]}` validates.
- AC2 (R1): Each of these 9 fixture declarations is refused at load with 0 transports opened and a
  diagnostic naming `triggers.<input>.types[i]` and the offending sub-path: a name equal to `keyword`;
  a name of 33 characters; empty `kinds`; a kind `banana`; both `match` and `hook: true`; an operator
  `regex`; a `field` path `secret.token` absent from canonical and declared fields; a `param` naming a
  property absent from the schema; a tree of depth 9.
- AC3 (R1): A declared type with neither `match` nor `hook` is refused with a diagnostic containing
  `triggers.<input>.types[0].name`; a configured parameter outside the declared schema is refused
  naming `triggers.<input>.policy.rules[0].parameters.<key>`; a `probability_parameter` naming a
  parameter the schema does not list as required is refused naming
  `triggers.<input>.types[0].probability_parameter`; no diagnostic contains the configured value.
- AC4 (R2): With the AC1 type and policy, events with `payload.badge.tier` `gold`, `silver`, absent,
  and `7` (integer) decide `accepted:badge_tier_match`, `rejected:badge_tier_no_match`,
  `rejected:badge_tier_no_match`, `rejected:badge_tier_no_match`; an event of kind `cue` decides
  `rejected:badge_tier_kind_mismatch`; with `badge_tier{tiers: ["gold", "silver"]}`, `silver` decides
  `accepted:badge_tier_match` and `Gold` decides `rejected:badge_tier_no_match`.
- AC5 (R2): A self-authored event, an internal trace and a non-schema-2 event are rejected by their
  existing invariant reasons with `rule_evaluations` unchanged (delta 0); a redelivery inside the dedup
  window returns the recorded decision with 0 additional rule evaluations and 0 draws.
- AC6 (R2): A per-call policy override naming `badge_tier` on an event whose `metadata.source` is a
  second input that does not declare it raises a ContractError naming that input; the declaring
  input's rule is evaluated 0 times.
- AC7 (R2): A declared type with a `probability_parameter` evaluated over 100 events (50 matching the
  tree, 50 not) with a recorded random source consumes exactly 100 draws; replaying the same 100
  events with the same source yields the identical 100 decisions; an event whose tree matched and whose
  draw is ≥ the probability decides `rejected:<type>_probability_miss`.
- AC8 (R2): `matches` with pattern `hi*bot?` matches `Hi there BOTs` and does not match `hi bot`; a
  141-character pattern is refused at load; evaluating a pattern of 128 `*` characters over a
  10 000-character text completes, and decides, on the injected clock with no timeout path involved.
- AC9 (R2): An accepted declared-type decision followed by a full scheduler leaves the event not
  admitted with the scheduler's own refusal; an accepted event's run offers no action that the existing
  authorization view does not grant (asserted with a granting-rule-free configuration: 0 offered
  writes).
- AC10 (R2, R4): The existing `tests/test_triggers.py` suite, unmodified, passes: a policy of generic
  types decides exactly as before.
- AC11 (R3): A hooked fixture type whose hook returns `True`, `False`, `1`, `"yes"`, `None` and
  raises yields respectively `accepted:<t>_match`, `rejected:<t>_no_match`, and four
  `rejected:<t>_hook_error`; each is counted as a trigger decision and retained in the dedup window.
- AC12 (R3): With `hook_timeout_seconds: 0.5`, a hook that never returns yields
  `rejected:<t>_hook_timeout` once the engine's ManualClock has advanced 0.5 s, and a following event
  of the same input is evaluated normally; a declared bound of 0 or 10.5 is refused at load.
- AC13 (R3): The hook is invoked 0 times for an invariant-rejected event, a dedup redelivery, an event
  of a kind outside the type's `kinds`, and an event from another input; 1 time for an eligible event.
- AC14 (R3): A module supplying a hook for a type declared by another module is refused, and that
  type's evaluation then decides `rejected:<t>_hook_unavailable`; with no hook supplied at all the
  decision is the same.
- AC15 (R4): `EVENT_KINDS[:10]` equals the phase-7 tuple, `EVENT_KINDS[10:]` equals
  `("reward", "manual", "speech", "timer")`, `len(EVENT_KINDS) == 14`, `len(PLATFORM_NOTICE_KINDS) ==
  7`, and `input_family_of_event` returns `None` for a `channel.chat.message` of each new kind.
- AC16 (R4): A brain route with `match.kinds: [manual]` validates at startup; each shipped `*.example`
  profile, run against fakes on a fixed event sequence, produces the same decisions (reason and
  policy version), subscriptions and admissions as the phase-7 tree — 0 differences; the runtime
  dependency list is exactly `aiohttp` and `PyYAML`.
- AC17 (R5): With `notices.kinds` unset, the fake EventSub records 0 reward subscriptions; with
  `[reward]`, exactly 1 of type `channel.channel_points_custom_reward_redemption.add`, version `1`,
  condition `broadcaster_user_id` equal to the configured broadcaster.
- AC18 (R5): A refused reward subscription produces exactly 1 `module.degraded` naming `reward`, chat
  messages are still published, and the degraded payload contains no token or broadcaster credential.
- AC19 (R5): A recorded-shape redemption with `user_input` `"  hello  "`, reward `{id: r1, title:
  Hydrate, cost: 500}` normalizes to kind `reward`, text `hello`, `payload.reward == {id: r1, title:
  Hydrate, cost: 500}`; with empty `user_input` its text is `Hydrate`; the same redemption id delivered
  twice yields 1 decision; 3 malformed redemptions (no id, no reward id, cost `"500"`) and 1 with user id
  `a:b` publish 0 events and add 4 to `counts["invalid"]`.
- AC20 (R5): Policy `reward{reward_ids: [r1], min_cost: 100, max_cost: 1000}` accepts cost 500 on
  `r1`, rejects cost 50 on `r1`, cost 2000 on `r1`, and cost 500 on `r2`; `reward{reward_ids: [r1]}`
  accepts `r1` at cost 0 and at cost 100 000.
- AC21 (R6): With `page`, `hotkey` and `overlay` enabled and a 40-character token, 0 listeners are
  open before `start_inputs`; after it, a press of configured button `hype` from each of the page, the
  press command — `modules.operator.__main__.main(["press", "hype", "--profile", <fixture>],
  opener=<fake>)` as in AC40, whose fake opener hands the request to the input's request core and
  returns the core's answer — and the overlay produces 3 `manual` events, each
  with `payload.button.id == "hype"`, the matching `surface`, the button's `prompt_text`, author
  `operator:hype`, distinct `message_id`s and the configured destination; each is decided and, when
  accepted, admitted through the scheduler. After `stop_inputs`, the listener is closed and a press
  of `hype` from each surface produces 0 events.
- AC22 (R6): Requests with no token, a wrong token, a wrong `Host`, a foreign `Origin` on a press, an
  unknown button, and a second press of `hype` 1 s after the first (cooldown 2) each produce 0 events
  and increment a refusal count; a press 2 s after the last accepted one is accepted. Across all of
  these, neither the token nor any request header value appears in a trace, diagnostic or log record.
- AC23 (R6): Each of: no surface enabled; a 31-character token; an empty token; a button without
  `prompt_text`; bind address `0.0.0.0` without the non-loopback setting — is a startup diagnostic
  naming the field, with 0 listeners opened; a profile that does not enable `operator` opens 0
  listeners.
- AC24 (R6): The operator default policy accepts a `manual` event; `button{button_ids: [hype]}` accepts
  `hype` and rejects `other` with `rejected:button_no_match`; no Streamer.bot module is enabled in any
  of AC21–AC24.
- AC25 (R7): With `listen` absent or `enabled: false`, 0 detector or source invocations occur over 60 s
  of the injected clock and the manifest-level behaviour of `audio.capture` is the phase-7 one (its
  existing tests pass unmodified).
- AC26 (R7): With listening enabled, a fake detector reporting the phrase `nova`, a fake source
  writing 1 WAV and a fake transcription answering `"hey nova what is this"`, under policy
  `keyword{keywords: [nova]}`: the candidate (text `nova`, no transcription) decides
  `accepted:keyword_match` while the source has been invoked 0 times and the transcription called 0
  times; the source is then invoked once with `AUDIO_INPUT_PRE_ROLL_SECONDS=2` in its environment, the
  transcription is called once, and exactly 1 `speech` event is published with that text, author
  `voice:streamer`, the candidate's `message_id` and the configured destination, admitted with 0
  further rule evaluations and 0 hook invocations. Under `keyword{keywords: [banana]}` the candidate
  decides `rejected:keyword_no_match`, the source is invoked 0 times, the transcription called 0 times
  and 0 events are published. A transcription failure and an empty transcription each publish 0
  events and increment a count; a source WAV over `max_bytes` is a counted source failure, transcribed
  0 times. *Observed bounds.* The fake source synthesises its WAV from a reference timeline on the
  injected clock that carries a marker tone at the instant the phrase begins, and writes the window
  the module's environment asks for; the criterion measures the WAV handed to the transcription. With
  `listen.max_seconds: 8` the source receives `AUDIO_INPUT_MAX_SECONDS=8` and the transcribed WAV
  lasts at most 8 s by its header; a source WAV of 8.5 s is a counted source failure, transcribed 0
  times; with `listen.pre_roll_seconds: 0` the transcribed WAV starts at the verdict (observed start
  offset 0 s); with the phrase beginning 1.5 s before a late verdict and `listen.pre_roll_seconds: 2`,
  the transcribed WAV starts 2 s before the verdict, the marker lies 0.5 s into it and the phrase is
  wholly inside it, while with `listen.pre_roll_seconds: 1` the same late verdict yields a WAV starting
  1 s before the verdict, without the marker. `listen.max_seconds: 0`, `listen.max_seconds` above the
  module's `max_seconds`, `listen.pre_roll_seconds: 6`, and `listen.pre_roll_seconds` not less than
  `listen.max_seconds` are each a startup diagnostic naming the key, with 0 processes started. With a
  fake detector detecting on every invocation, an accepting policy and a transcription held pending,
  the detector has been invoked exactly 1 time until the first `speech` event is published; releasing each transcription and advancing the clock 1 s after each publication, the 3
  events are published in detection order and the detector is invoked 4 times in all (the 4th still
  pending); after `stop_inputs`, 0 further invocations start.
- AC27 (R7): A fake detector `command` exiting 0 with standard output `hey nova` submits exactly 1
  candidate, with `payload.wake_word == {detected: true, phrase: "hey nova"}` and text `hey nova`,
  which the default policy decides `accepted:wake_word_match`; exiting 1, exiting 2, and exiting 0
  with a 65-character phrase each submit 0 candidates and invoke the source 0 times (the last two
  counted as detector failures); an HTTP detector answering `{"detected": true, "phrase": "hey
  nova"}` submits 1 candidate decided `accepted:wake_word_match`, and `{"detected": "yes"}` and status
  500 each submit 0 (counted). The command detector's standard input is empty and the HTTP request
  carries no audio. A candidate submitted to the engine with `payload.wake_word == {detected: false}`
  decides `rejected:wake_word_no_match`, one with no `wake_word` decides
  `rejected:wake_word_hook_error`, and a `speech` event carrying a transcription is never passed to
  the hook (0 invocations across this criterion's published events). With the module's hook replaced
  by one that never returns, the candidate decides `rejected:wake_word_hook_timeout` after 2 s of the
  injected clock; with no hook supplied, the default policy decides
  `rejected:wake_word_hook_unavailable` and 0 runs are admitted. Enabling listening with no
  `wake_word.detector` is a startup diagnostic naming `wake_word.detector`, with 0 processes started.
- AC28 (R7): After 5 accepted candidates, the attachment store holds 0 entries, the module wrote 0
  files, and no bus-history or audit record, candidate or `speech` event contains WAV bytes or a
  base64 of them; the detector received no audio from the core, and the transcription received each
  of the 5 acquired WAVs exactly once.
- AC29 (R8): With policy `interval{every_minutes: 10, probability: 1}` and 2 configured channels,
  advancing the ManualClock 60 minutes after `start_inputs` yields exactly 12 `timer` events (6 per channel) with sequences
  1–6 per channel and `message_id` `timer:<platform>:<channel_id>:<sequence>`, each with `text` equal to its channel's
  `prompt_text`, all accepted.
- AC30 (R8): With `probability: 0.25` and a recorded random source, 100 timer events consume exactly
  100 draws and the accepted count equals the number of draws below 0.25; with window `{start_minute:
  600, end_minute: 660}`, events at `minute_of_day` 599, 600, 660 and 661 decide
  `rejected:interval_no_match`, accepted, accepted, `rejected:interval_no_match` (probability 1);
  without a window, events at `minute_of_day` 0 and 1439 are accepted (probability 1). The shipped
  `interval` declaration registers through R1's load checks with no hook, and a fixture declaration
  whose rule references `{param: window.nope}` is refused at load naming that path.
- AC31 (R8): `every_minutes` 0 and 1441, `probability` 1.5, a window with start 700 > end 600, and a
  configured channel whose resolved policy holds 0 or 2 `interval` rules are each a startup diagnostic
  naming the input and field; advancing the clock 60 minutes after `prepare` and before `start_inputs`
  publishes 0 events, and so does advancing it 60 minutes after `stop_inputs`.
- AC32 (R9): `docs/triggers.md` and `docs/triggers.fr.md` exist and are a discovered bilingual pair;
  the English guide contains one configuration example per family (4), the sentence that a configured
  policy replaces the default, and the out-of-scope statement on continuous listening;
  `docs/campaigns/phase8/trial-record.md` exists with status `not run`; `summary.md` lists R1–R9 each
  with a commit and a test identifier.
- AC33 (R9): The full suite runs with network access blocked at the socket layer (a test fixture that
  makes socket creation raise) and passes, and no test spawns a process outside a fake runner.
- AC34 (R3): With a hooked fixture type `<t>` under the policy `{combination: none_of, rules: [<t>]}`,
  a hook that raises decides `rejected:<t>_hook_error`, a hook that never returns decides
  `rejected:<t>_hook_timeout` once the bound has elapsed on the ManualClock, and no hook supplied
  decides `rejected:<t>_hook_unavailable`; under `{combination: any_of, rules: [keyword{keywords: [hi]},
  <t>]}`, an event with text `hi` whose hook raises decides `rejected:<t>_hook_error`, and the same with
  `<t>` as the module's default policy decides the same. In every case 0 events are admitted.
- AC35 (R2): A declared fixture type with `probability_parameter: p`, under the policy `<t>{p: 0.3}`
  and a recorded random source whose values are `0.9, 0.1, 0.42`, evaluated on 3 tree-matching events
  decides `rejected:<t>_probability_miss`, `accepted:<t>_match`, `rejected:<t>_probability_miss`, and
  the third decision's `draws` equals `({rule: 0, parameter: "p", probability: 0.3, draw: 0.42,
  ordinal: 2},)`; replaying the three events from a source re-seeded to the same values yields equal
  `draws`; a redelivery of the third returns the same `draws` with 0 draws taken; a decision of a
  generic-only policy (including `probability`) has `draws == ()`; no entry of `draws` contains a
  payload field, the event text or a parameter other than `p`.
- AC36 (R2): For the 128-character pattern of 63 `*a` pairs followed by `*b`, evaluated over texts of
  5 000 and 10 000 `a` characters, each evaluation decides no match and the matcher's step count is at
  most (len(text) + 1) × 129; and steps(10 000) ≤ 2 × steps(5 000) + 129, so an implementation whose
  steps grow quadratically in the text, or that backtracks recursively, fails this criterion. The
  engine's `counts["wildcard_steps"]` grows by exactly the matcher's reported steps.
- AC37 (R7): With listening enabled, after `prepare` completes and before `start_inputs`, the fake
  runner records 0 detector or source invocations and 0 processes, the engine 0 candidates, the fake
  transcription 0 calls, and the bus 0 `speech` events; after `start_inputs`, exactly 1 detector
  invocation and 0 source invocations. A startup in which another enabled module's `prepare` fails
  records 0 invocations and 0 processes for `audio_input`. In `stop_inputs` an in-flight detector
  invocation is terminated (the fake runner records its termination) and 0 candidates follow; an
  in-flight acquisition is terminated likewise, its partial WAV is discarded and 0 transcriptions
  follow; and advancing the clock 600 s starts 0 invocations.
- AC38 (R7): With listening enabled, `retry_seconds: 5` and a fake detector that returns without
  detecting (exit 1) on every invocation, advancing the injected clock 3 600 s submits 0 candidates,
  invokes the source 0 times, makes 0 transcription calls and publishes 0 events, with exactly 17
  detector invocations (at 0, 5, 15, 35, 75, 155 and 315 s, then every 300 s); with a fake detector
  that detects on exactly 4 of its invocations over the same span, an accepting policy makes exactly 4
  source invocations and 4 transcription calls, and a declining policy 0 of either; a transcription
  error on one of them is followed by no second transcription of that segment and by no detector
  invocation before 5 s.
- AC39 (R7): With `retry_seconds: 5`, a fake detector that detects on every invocation and an
  accepting policy: after (a) a transcription error, 0 detector invocations have started at 4.9 s and
  exactly 1 at 5 s; a second consecutive error is followed by 0 at 9.9 s and 1 at 10 s; after (b) an
  empty transcription the delay is that of the same consecutive count; after (c) a published `speech`
  event — and, under a declining policy, a declined candidate — 0 at 0.9 s and exactly 1 at 1 s, and a
  transcription error following (c) is again followed by 5 s, not 20 s; a detector failure (exit 2)
  and a source failure each back off the same way; over 20 consecutive detector failures no two
  invocations are more than 300 s apart, and exactly 1 `module.degraded` with reason
  `wake_word_detector_failed` and no configured value is reported, while an exit 1 from `healthy` reports none.
  *Recovery.* Over two runs of detector failures (exit 2) separated by one invocation that returns
  without detecting (exit 1) — 3 failures, 1 verdict, 2 failures — the health reports for
  `audio_input` are, in order, exactly `module.degraded` (`wake_word_detector_failed`, at the first
  failure), `module.ready` (at the verdict) and `module.degraded` (at the first failure of the second
  run): 0 reports at the 2nd and 3rd failures of the first run and at the 2nd of the second, so no
  duplicate between the transitions; a detected verdict in place of the exit 1 yields the same three
  reports, and a run that ends in a source failure or a transcription error after its verdict adds
  none. An
  HTTP detector whose endpoint refuses the connection is a detector failure backed off the same way;
  and a detector invocation held pending for 3 600 s of the clock is neither terminated nor counted a
  failure.
- AC40 (R6): `modules.operator.__main__.main(["press", "hype", "--profile", <fixture>], opener=<fake>)`
  sends exactly 1 POST to the configured listen address's press endpoint naming `hype` with surface
  `hotkey` and the token, and returns 0 when the fake answers accepted; it returns 2 for each refusal
  the request core gives (unknown button, cooldown, wrong token, `hotkey` disabled — each with 0
  events), 3 when the fake raises a connection error or a timeout, and 64 with no button argument or
  an unreadable profile; its standard output and error never contain the token. Importing
  `modules.operator.__main__` in-process with `modules.operator`, `modules.operator.__main__`,
  `core.runtime`, `core.loader` and `core.lifecycle` first removed from `sys.modules` leaves the last
  three absent.

## Caller enumeration

Search method: `grep -rn` over `core/`, `modules/` and `tests/` for `TriggerTypeDeclaration(`,
`TriggerSpec(`, `registry.register(` / `.register(name, declaration.triggers`, and `engine.evaluate(`.
Blind spots: dynamic dispatch through the runtime facade (`core/runtime.py` `evaluate`) is listed by
hand; fixture manifests under `tests/fixtures/modules` reach the loader without a code call site.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/triggers.py | `BUILTIN_TRIGGER_TYPES` (4 `TriggerTypeDeclaration(...)`) | No change: the new fields are optional and absent on a generic type. |
| core/loader.py | `_trigger_spec` (builds `TriggerTypeDeclaration`, `TriggerSpec`) | Parses and passes the new keys; `_TRIGGER_TYPE_KEYS` grows. |
| core/loader.py | `registry.register(name, declaration.triggers, companion_name=...)` | Signature unchanged; registration now accepts declared types. |
| core/runtime.py | `register` facade over `TriggerRegistry.register`; `evaluate` facade | Signatures unchanged; the hook-supply entry for a module is new (0 existing callers). |
| modules/twitch, kick, youtube, streamerbot, watch `__init__.py` | `engine.evaluate(event, context=...)` | Signature unchanged; no migration. |
| tests/test_triggers.py, tests/test_twitch.py | direct `TriggerTypeDeclaration(...)` / `TriggerSpec(...)` | Unchanged: positional/keyword fields they use keep their meaning. |

## Test failure allowlist

Each entry fails only because 4 kinds are appended, 2 modules are added, `audio_input` becomes an
input, `twitch` declares one more type, or one guide pair is added; the adaptation updates expected
constants and loosens nothing.

- `tests/test_contracts.py::test_event_kind_vocabulary_is_the_nine_names_in_order` — asserts 10 kinds
  ending in `cue`; R4 appends 4 (14, ending in `timer`, first 10 unchanged).
- `tests/test_input_ownership.py::test_cue_is_the_tenth_kind_and_not_a_platform_notice` — asserts
  `EVENT_KINDS[-1] == "cue"`; R4 makes `cue` index 9 and the last kind `timer`.
- `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` — asserts 19
  manifests, the module-name set, the twitch type list of 4 and the `audio_input` declaration keys; R6
  and R8 add `operator` and `timer` (21), R5 adds `reward` to twitch's types, R7 adds `triggers` and
  the `input` role to `audio_input`.
- `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest` — 19 → 21 (R6, R8).
- `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`
  — 19 → 21 (R6, R8).
- `tests/test_profiles.py::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency` —
  19 → 21 manifests and package-data lines (R6, R8); the dependency assertion is unchanged.
- `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin` — 19 → 21 (R6, R8).
- `tests/test_docs_bilingual.py::test_the_discovered_pairs_are_exactly_the_declared_guides` — the
  declared set gains `triggers` (R9).
- `tests/test_audio_input.py::test_manifest_declares_an_on_demand_read_with_no_role` — asserts
  `produces: []`, no lifecycle role and the phase-7 settings keys; R7 makes `audio_input` produce
  `channel.chat.message`, take the `input` role and gain `listen` and `wake_word`; its assertions on
  the `audio.capture` action stay exact.
