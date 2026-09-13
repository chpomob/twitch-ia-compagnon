---
name: "phase1-agentic-vertical"
version: "1.2"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec]
targets:
  - file: core/contracts.py
    description: "Add typed observation parts (`text`, `image_ref`) to `ActionObservation` with an empty default, their per-part validation, the observation-size rule (text UTF-8 bytes + image_ref `size`), the optional `delivery` capability of an action declaration (`text_argument: <name> | none`, non-`read` actions only, part of the compared spec), and the proxy/adapter error-code vocabulary shared by brain, proxy and agent link."
  - file: core/actions.py
    description: "Validate observation parts on every terminal observation (part shape, text bound, `image_ref` leased to the call's run and unexpired) and classify a provider that becomes not-ready mid-run as `refused` with `provider_not_ready`, provider uninvoked."
  - file: core/admission.py
    description: "Add an optional stale-drop hook the scheduler calls exactly once per `stale_drop`, with the work, session and remaining total budget, so the brain can apply the fallback policy without the scheduler knowing what a fallback is."
  - file: core/main.py
    description: "Resolve the reserved `modules_directory: builtin` to the installed `modules` package directory and add `--check-config` (validate configuration, manifests and module settings hooks, open no transport, exit 0/2); still no module name."
  - file: modules/brain/__init__.py
    description: "Replace the single-turn engine by the multi-turn loop: tool-based action proposals restricted to catalog `read` actions (non-read or unknown names refused without an executor call), one action per turn, observations (text and image) appended to a bounded transcript, final response delivered through the executor by the configured, ordered delivery list (fixed or module-derived, resolved and validated at prepare, one call per entry, text passed only where declared), acted budgets, repeated-action detection, generic fallback, prepare-time backend capability probe, image resolution at call time only."
  - file: modules/brain/module.yaml
    description: "Extend `settings_schema` with `budget.max_action_calls`, `budget.max_repeated_actions`, `budget.delivery_reserve_seconds`, the `fallback` group, the `capabilities.required` list and the `delivery` group (`mode`, `actions[]`, `preference[]`, `overrides`); validator name unchanged."
  - file: modules/twitch/module.yaml
    description: "Declare the delivery capability on `chat.write` (`delivery: {text_argument: text}`); name, nature, schemas, destinations and permission unchanged."
  - file: modules/chat_context/__init__.py
    description: "New module providing the read action `chat.read` over the runtime's per-channel chat context, with dated messages and an explicit coverage report."
  - file: modules/chat_context/module.yaml
    description: "Manifest v2 declaring `chat.read` (read, permission `chat.read`, destinations `*/*/chat`), settings schema and validator, no grant."
  - file: modules/users/__init__.py
    description: "New module consuming `channel.chat.message` into a bounded per-(platform, channel) user directory and providing the read action `users.read` with cursor pagination, freshness and honest coverage."
  - file: modules/users/module.yaml
    description: "Manifest v2 declaring `consumes: [channel.chat.message]`, the `users.read` action (read, permission `users.read`, destinations `*/*/chat`), directory bounds schema and validator."
  - file: modules/capture/__init__.py
    description: "New module providing the read action `screen.capture` from configured capture sources (`file`, `command`), storing bytes in the run-leased attachment store and returning an `image_ref` part; source readiness checked at prepare."
  - file: modules/capture/module.yaml
    description: "Manifest v2 declaring `screen.capture` (read, permission `screen.capture`, destinations `*/*/capture`), sources schema and validator, no grant."
  - file: modules/proxy/__init__.py
    description: "New brain-side module: WebSocket JSON server accepting exactly one paired agent, binding itself as the provider of the allowlisted remote actions from the discovered catalog, forwarding calls/cancellations, receiving observations and attachments by reference into the local store, and publishing authenticated, normalised remote events through a source adapter."
  - file: modules/proxy/module.yaml
    description: "Manifest v2 with lifecycle role `input`, settings schema (`listen`, `tls`, `pairing_token` credential, `actions` allowlist, frame/heartbeat/late-result bounds) and validator; declares no action itself."
  - file: modules/agent_link/__init__.py
    description: "New agent-side module: WebSocket JSON client dialing the brain with the pairing token, exponential backoff with injected RNG/clock, executing forwarded calls through the local default-deny executor, transferring attachments, bounded call de-duplication (per-session `last_seq` plus TTL/size-bounded call table, reset on `welcome`)."
  - file: modules/agent_link/module.yaml
    description: "Manifest v2 with lifecycle role `input`, settings schema (`brain_url`, `pairing_token` credential, `agent_id`, `actions`, `reconnect`, heartbeat, call table, frame bound) and validator."
  - file: config.yaml.example
    description: "Becomes the PC profile: enables twitch, chat_context, users, capture, brain, audit; carries the acted budget defaults, fallback and capabilities settings, the `delivery` group as the single fixed entry `chat.write`, and explicit grants for `chat.read`, `users.read`, `screen.capture`, `chat.write`; 0 literal secrets."
  - file: config.server.yaml.example
    description: "New server profile: brain, platforms/sinks, chat_context, users, proxy (TLS listen, pairing token reference, `actions: [screen.capture]`), no capture module; same grants, same single-entry `delivery` group; 0 literal secrets."
  - file: agent.yaml.example
    description: "New PC-agent profile for the same entry point: enables capture and agent_link, agent-side default-deny grant for `screen.capture` to principal `brain`, attachment limits; 0 literal secrets."
  - file: pyproject.toml
    description: "Package `core*` and `modules*` with every `module.yaml` as package data and expose the `twitch-ia-compagnon` console script; runtime dependencies stay `aiohttp` and `PyYAML` only."
  - file: docs/proxy-protocol.md
    description: "Normative description of proxy protocol v1: frame envelope, frame types, correlation (hello nonce, session_id, call_id), per-session `seq` and the agent's bounded call-identity rule, limits, error codes, close codes, deadline and attachment transfer rules, as fixed by this spec."
  - file: docs/README.md
    description: "Add the phase 1 versioning section (which phase 0 / v1 assertions change and by what) and the PC/server topology trial record (what was run, on which commit, with which profiles, outcome, limits)."
  - file: tests/conftest.py
    description: "Make the shared fake model transport answer the prepare-time capability probe and keep probe requests out of per-run request counts; add shared doubles for a scripted tool-calling model, an injectable capture source and an in-memory WebSocket pair."
  - file: tests/fixtures/modules/fakeplatform/__init__.py
    description: "Second, fictional platform module: produces schema-version-2 `channel.chat.message` for platform `fake`, provides `chat.write` for `fake/*/chat`, declares a default keyword trigger."
  - file: tests/fixtures/modules/fakeplatform/module.yaml
    description: "Manifest v2 of the fictional platform, mirroring the twitch declarations for platform `fake`, `chat.write` carrying the same delivery capability."
  - file: tests/fixtures/modules/fakeeffects/__init__.py
    description: "Fictional delivery module: provides the write actions `audio.say` (delivery, text argument `text`) and `stream.set_scene` (delivery, no text argument, argument `scene`) and the plain write action `overlay.raw` (no delivery capability); every provider records its invocations and can be scripted to fail."
  - file: tests/fixtures/modules/fakeeffects/module.yaml
    description: "Manifest v2 declaring the three write actions above (destinations `*/*/audio`, `*/*/stream`, `*/*/overlay`), the `delivery` capability on the first two only, no grant."
  - file: tests/test_brain.py
    description: "Adapt the harness to the probe and the tool-based request shape; rewrite the two allowlisted prompt assertions to the phase 1 guarantee (read actions offered as tools, delivery never offered)."
  - file: tests/test_examples.py
    description: "Rewrite the three allowlisted assertions to the PC profile (six enabled modules, four declared actions, grants equal to the provided-action set) and add the same checks for the server profile (provided-action set = manifests ∪ proxy `actions` allowlist) and the agent profile."
  - file: tests/test_agentic_loop.py
    description: "New: scripted scenario chat.read → text → screen.capture → image → final → chat.write on twitch and on the fake platform, local provider and simulated proxy boundary; one-action-per-turn refusal; failure and cancellation matrix."
  - file: tests/test_delivery.py
    description: "New: the delivery list contract — single-entry phase 1 configuration identical to today, fixed two-entry text + effect list, two text entries, module-derived list and preference order, per-destination override, preparation failures per reason before the barrier, per-entry outcomes without a false confirmed send, fallback through the list, and the no-change-to-the-loop proof."
  - file: tests/test_model_adapter.py
    description: "New: capability probe success/refusal per capability, request shape (tools, structured proposal, image encoding at call time), response shape classification, redaction."
  - file: tests/test_budgets.py
    description: "New: every acted budget and the fallback policy (enabled, disabled, unauthorized, out of bounds, stale_drop), with injected clock, no positive sleep."
  - file: tests/test_observations.py
    description: "New: observation parts validation, store lease and cleanup after run, too-large and expired images, executor validation of `image_ref`."
  - file: tests/test_chat_context_module.py
    description: "New: `chat.read` contract, bounds and coverage on both platforms, default-deny without grant."
  - file: tests/test_users.py
    description: "New: directory bounds, `users.read` pagination, freshness, coverage never complete, roles only with provenance, default-deny."
  - file: tests/test_capture.py
    description: "New: sources `file` and `command`, readiness at prepare, size refusal, image_ref result, no capture without grant."
  - file: tests/test_proxy.py
    description: "New: in-process protocol tests over an in-memory transport: envelope, hello/welcome correlation and session ids, versioning, auth, one-agent limit, call/observation/cancel, `seq`-based call identity and table eviction, attachments by reference, deadline propagation with skewed clocks, drop before/after effect, late/duplicate observations, event source adapter, backoff sequence."
  - file: tests/test_proxy_process.py
    description: "New: two real processes over loopback WebSocket: pairing valid/invalid, one agent per brain, the scripted scenario end to end, reconnection after a kill, attachment transfer without local paths."
  - file: tests/test_profiles.py
    description: "New: `--check-config` on the three example files with dummy environment, transport selection (local vs remote, ambiguity refused), clean-environment install and `builtin` discovery of 8 manifests."
  - file: tests/test_hygiene.py
    description: "New: whole-suite grep asserting no positive-duration `asyncio.sleep`/`time.sleep` in `tests/`, and no model name literal in `core/` or `modules/`."
---

# Phase 1 — Agentic text + image vertical, local and remote

## Problem

Phase 0 delivered the foundation: bounded admission, per-input triggers, phase
lifecycle, an action registry/executor with default-deny authorization, a
run-leased attachment store, bounded retention and correlated supervision. Its
brain is still a **single-turn** engine: one model call, one plain-text reply,
one `chat.write` delivery. The companion therefore cannot look at the chat,
cannot look at the screen, and cannot decide to answer after observing.

Phase 1 turns the brain into the multi-turn run engine of design-v2 §3.1: the
model proposes one structured action per turn, the executor runs it, the
observation (text and/or image) returns to the model, and the final response is
delivered through the same executor. It adds the four capabilities the model
needs for that (`chat.read`, `users.read`, `screen.capture`, `chat.write`), the
acted budgets of §3.4 with the generic fallback, multimodal observations on the
existing attachment store, and — because the brain may live on a server while
the screen is on the streamer's PC — a small PC agent and its WebSocket JSON
proxy, in two distinct deployment profiles, without distributing the bus.

Authority: `docs/design-v2.md` §3.1–3.4, §4.1–4.4 and §6 "Phase 1". The
historical v1 spec remains readable at `git show c5754e1:spec.md`; this file
supersedes nothing of phase 0 except what the allowlist below names.

### Decisions this spec settles (design §6 delegated them to implementation)

Each is a **contract**, not a mechanism; the plan chooses the code.

1. **Delivery is a configured, pluggable terminal step.** In phase 1 the
   model is offered, as tools, the authorized actions of nature `read` only.
   The final response is a plain assistant message; the runtime then performs
   the run's **delivery**: a configured, ordered list of delivery actions,
   invoked with principal `brain` only once the final answer exists — never
   during reasoning. The list is configured per case (per streamer, input or
   channel configuration) in one of two modes: (a) `fixed` — an explicit
   ordered list of action names; (b) `modules` — the ordered list is derived
   from the enabled modules whose actions declare a delivery capability, in a
   configurable preference order, so enabling a module (audio output,
   stream/scene control) adds a delivery without touching the agentic loop.
   Each entry declares how the final answer text maps into that action's
   arguments: the name of a text argument (e.g. `text` for `chat.write`), or
   no text argument for an effect-only delivery (a stream-scene or
   configuration change). Every entry of the list is invoked at the terminal
   step, each receiving the answer text only if its declaration says so:
   `[chat.write, audio.say]` writes the message *and* speaks it;
   `[stream.set_scene]` performs an effect with no text. One terminal delivery
   step per run and no intermediate effect (§3.1): during reasoning the model
   is still offered only actions of nature `read`, and a proposal naming an
   action whose catalog nature is not `read` (`chat.write` included, whatever
   the brain's grants) is refused by the runtime itself, executor uninvoked —
   this restriction is unchanged. The model never selects the delivery action;
   the configured policy does. Delivery actions are declared by modules like
   any other action, and the list is validated at preparation, before the
   readiness barrier: an entry naming an unknown action, an action whose
   nature or capability is not a delivery, or a missing or ambiguous text
   mapping fails preparation naming the entry, and no scenario runs. Phase 1
   ships the mechanism configured with the single entry `chat.write`, so the
   observable behaviour is unchanged, and phase-2 delivery modules plug in
   without any change to the agentic loop. Delivery outcomes use the existing
   explicit terminal vocabulary, one outcome per entry in the run's
   accounting: an uncertain delivery is never memorised as a confirmed send,
   and a failing entry never silently drops the others. *Rationale:* one
   terminal step per run, enforced structurally rather than by prompt or by
   grants, whose targets are policy rather than code.
2. **Response shape classification.** A backend response whose message carries
   exactly one tool call is an action proposal; a message with text content and
   no tool call is the final response; anything else (≥ 2 tool calls, both a
   tool call and content, neither) is an unsupported shape that ends the run
   `error` with no action executed. *Rationale:* Chat Completions expresses
   structured proposals natively as tool calls, so no proprietary schema is
   needed and multiple calls are detectable before any effect.
3. **Capability verification = prepare-time probe.** Before the readiness
   barrier the adapter sends at most 2 bounded probe requests to the configured
   endpoint: (i) a forced tool call on a probe tool; (ii) the same with one
   1×1-pixel PNG image part when `vision` is required. A capability is verified
   only by a 2xx response of the expected shape; otherwise the brain is not
   ready, startup fails naming the capability, and no scenario runs.
   *Rationale:* "compatible format" proves nothing (§3.1); a probe costs a few
   hundred tokens once per activation and fails before any viewer is answered.
4. **`chat.write` stays a platform-provided action.** The starter
   `modules/chat_output/` of §4.4 is not created: the twitch module (and the
   test platform) already serve `chat.write` behind the executor; a
   pass-through module would add a hop and no behaviour. Platform neutrality is
   proven by the second platform serving the same contract.
5. **`users.read` is a directory of observed authors.** `modules/users/`
   consumes the normalised chat events every platform already publishes and
   serves a bounded directory keyed by `(platform, channel_id)`. Coverage is
   reported as `observed_authors` with `complete: false` always: phase 1 never
   claims a full audience. *Rationale:* honest, platform-neutral, no new
   platform scopes; a fuller source can bind later behind the same contract.
6. **The agent is the same entry point with another profile.** `core.main.run`
   loads `capture` + `agent_link` from `agent.yaml`; no second runtime is
   written. The agent **dials** the brain (works from behind NAT); the brain's
   `proxy` module listens and accepts exactly one paired agent.
7. **Transport selection is by enabled modules, not by a new binding key.** A
   local provider is the module that registers the action in-process; a remote
   one is the `proxy` module, which binds itself for exactly the actions its
   `actions` allowlist names, using the spec from the brain's discovered
   catalog. Two providers for overlapping destinations fail preparation (phase
   0's ambiguity rule). The brain calls the executor either way.
8. **Proxy protocol v1** is fixed in R6 and `docs/proxy-protocol.md`:
   JSON text frames with `v`, `type`, `id`; attachments as a header frame plus
   one binary frame; nature-based classification of in-flight calls on a drop.
9. **Backoff**: exponential, initial 1 s, multiplier 2, cap 30 s, full jitter
   drawn from the injected RNG, reset on `welcome`, unlimited attempts while
   the agent runs.

### Standing constraints

- Phase 0 guarantees stay in force: bounded admission, phase lifecycle,
  terminal action outcomes, default-deny (reads included), bounded retention,
  one global startup and one global shutdown deadline, redaction of declared
  credentials and configured secrets in traces and loss diagnostics.
- No module name is added to `core/main.py`; every new module starts and stops
  through its manifest.
- Nothing in `core/` or in the brain depends on Twitch; nothing in `core/` or
  `modules/` hardcodes a model name or model-specific command.
- Runtime dependencies remain `aiohttp` and `PyYAML`.
- Tests never use a positive-duration sleep: clock, RNG, sleepers and
  transports are injected (enforced by a whole-suite grep, R8).
- An uncertain external outcome is never memorised as a confirmed send.

## Requirements

- R1: **Multi-turn agentic loop.** An admitted run iterates model turns. Each
  turn offers the model a bounded transcript (instructions, viewer message,
  retained conversation memory, previous proposals and their observations) and
  the *authorized* read actions for the run's destination and principal
  `brain`, re-read every turn. The model answers with either exactly one action
  proposal (name + JSON-object arguments) or a final response. For a proposal
  the runtime builds one `ActionCall` — destination `(run platform, run
  channel_id, the action's declared scope)`, principal, identities and deadline
  set by the runtime, never by the model — invokes the executor, appends the
  resulting observation to the transcript and continues if budgets allow.
  **Read-only execution restriction:** the runtime executes a proposal only if
  the named action exists in the discovered catalog with nature `read`; a
  proposal naming an action of any other nature (`chat.write` included, even
  though the brain holds a `chat.write` grant for delivery) or an action
  absent from the catalog yields a synthetic `refused` observation (code
  `not_a_read_action`, respectively `unknown_action`) with 0 executor calls
  and 0 provider invocations. A proposal naming a catalog `read` action
  outside the authorized view (no applicable rule), or with arguments the
  spec rejects, is passed to the executor and returns its `refused` /
  `error` observation (default-deny is the executor's, not the brain's); a
  proposal whose arguments are not a JSON object yields a synthetic `error`
  observation (code `malformed_arguments`) without an executor call. Every
  such proposal counts as a turn. A response containing two or more tool
  calls, or a tool call together with text content, is an unsupported shape:
  the run ends `error` (failure `unsupported_response_shape`) with 0 actions
  executed. **Terminal delivery step (decision 1):** the final response is
  delivered by invoking, once and in order, every entry of the run's resolved
  delivery list — one executor call per entry, destination `(run platform,
  run channel_id, the action's declared scope)`, principal `brain`,
  identities and deadline set by the runtime, arguments built by the runtime
  from the entry: the entry's constant `arguments` (a JSON object, default
  empty) plus, when the entry has a text argument, the final response text
  under that name; an effect-only entry receives no text. The list is
  resolved at `prepare` from the brain's `delivery` settings group, which
  holds a default and optional `overrides` of the same shape keyed by
  `<platform>/<channel_id>` (a run uses its destination's override, else the
  default; every configured list is resolved at `prepare`). `mode: fixed`
  takes `actions[]`, a non-empty list of entries `{action, text_argument?,
  arguments?}` where `text_argument` is an argument name, `none`, or absent
  (inherited from the action's declaration). `mode: modules` takes every
  action of the discovered catalog whose declaration carries the delivery
  capability, ordered by `preference[]` (the named ones first, in that
  order; the remaining delivery-capable actions after them in catalog order;
  a preference name matching no enabled delivery-capable action is ignored),
  each entry taking its text mapping from the declaration; the resolved list
  is published as a `brain.delivery.resolved` trace. The delivery capability
  is a field of the action declaration, `delivery: {text_argument: <name> |
  none}`, allowed on non-`read` actions only, and travels with the spec
  through the catalog (proxy-bound actions included). Resolution fails
  preparation, before the readiness barrier, with a diagnostic naming
  `delivery`, the entry (`delivery.actions[<i>]`, or the derived action name)
  and a reason ∈ {`unknown_action`, `not_a_delivery`,
  `text_mapping_missing`, `text_mapping_ambiguous`, `empty`}, when an entry
  names an action absent from the catalog; names an action of nature `read`
  or one without the delivery capability; expects the text under an argument
  absent from the action's argument schema, or under any argument while the
  declaration says `none`; names a text argument different from the
  declaration's, or carries a constant argument under the text argument's
  name; or when the resolved list is empty. No scenario runs after such a
  failure. The model never selects a delivery action and delivery actions
  are never offered as tools. Each entry is invoked whatever the outcome of
  the previous ones, has its own terminal outcome and counts one action
  call; the record carries `deliveries[]{action, call_id, text, status}` in
  list order (`text` true iff the entry received the final text; `status`
  the entry's terminal status, or `skipped:<reason>` per R3), and `delivery`
  summarises them: the single entry's status when the list has one entry,
  otherwise `success` when every entry is `success` and the first
  non-`success` status in list order otherwise. Conversation memory is
  written only when every entry that receives the text ended `success`; an
  `external_unknown` entry is never memorised as a confirmed send. The
  transcript and every leased attachment are released at run end. Call ids
  are `<run_id>/call-<n>` with `n` counting from 1 across actions and
  delivery entries, in invocation order. Run lifecycle traces stay owned by
  the scheduler; `brain.run.completed` carries `turns`, `action_calls`,
  `tokens`, `tokens_estimated`, `fallback`, `deliveries`.

- R2: **Single OpenAI-compatible adapter with verified capabilities.** One
  adapter speaks the Chat Completions format to the configured `endpoint`,
  `model` and `api_key`. Authorized read actions are sent as tool definitions
  (name, description, argument schema); the response is classified per
  decision 2. `capabilities.required` lists `structured_output` (mandatory;
  a list without it is refused at validation) and optionally `vision`. At
  `prepare`, before the readiness barrier, the adapter probes each required
  capability (decision 3) within `budget.model_call_seconds` per probe; a
  capability not verified makes the module not ready and stops startup with a
  diagnostic `module 'brain': backend capability '<name>' not verified:
  <reason>` where reason ∈ {`non_success_status`, `no_tool_call`,
  `multiple_tool_calls`, `malformed_arguments`, `image_rejected`,
  `timed_out`, `transport_failed`}; the diagnostic never contains the key,
  the endpoint or a response body. Image parts are resolved from the store and
  encoded for the backend only for the duration of a request; they never
  appear in traces or events. An `image_ref` observation reaching a run whose
  verified capabilities lack `vision` ends the run `error` (failure
  `capability_missing`, `capability: vision`) without sending the request and
  without dropping the image silently.

- R3: **Acted budgets and generic fallback.** Every profile configures finite,
  validated values for: `budget.model_turns` (default 5), `model_call_seconds`
  (30), `action_seconds` (10, delivery included), `max_tokens` (8192,
  cumulative input + output per run, backend usage when reported, otherwise a
  conservative estimate flagged `tokens_estimated: true`),
  `max_observation_bytes` (5 242 880), `max_action_calls` (6, every delivery
  entry and fallback included), `max_repeated_actions` (2: the (n+1)-th proposal of the
  same action name and canonical arguments within one run is not executed),
  `delivery_reserve_seconds` (10), `admission.wait_seconds` (30) and
  `admission.total_run_seconds` (60, admission → delivery, wait included; the
  wait must be ≤ the total). Exceeding turns, action calls, repeated actions or
  tokens ends the run `error` with `failure: budget_exhausted` and
  `budget: <name>`; a model call exceeding `model_call_seconds` ends the run
  `timeout`; an action exceeding `action_seconds` is the executor's `timeout`
  or `external_unknown` observation fed back to the model; no new model turn or
  non-delivery action starts when the time left before the total deadline is
  below `delivery_reserve_seconds` (the reserve covers the whole terminal
  step, every entry included); nothing is sent after the total deadline. At
  the terminal step a delivery entry for which no action call is left is not
  invoked and records `skipped:budget_exhausted`, one reached after the
  total deadline records `skipped:deadline_exceeded`; the entries before it
  keep their own outcomes, nothing is dropped silently.
  **Fallback:** when a run ends without a delivered final response because of
  budget exhaustion, deadline expiry or a queued `stale_drop`, and
  `fallback.enabled` (default `true`) holds, the runtime delivers
  `fallback.text` (default `"I could not answer in time."`) through the run's
  resolved delivery list exactly as the terminal step of R1 — `fallback.text`
  in place of the final response text, one executor call per entry with
  principal `brain`, each entry requiring an applicable rule and counting
  against `max_action_calls` and its provider's quotas; the run's `status`
  stays the failure status, `deliveries[]` carries each entry's outcome and
  `delivery` reports the fallback outcome distinctly (`fallback:<summary
  status>`, the summary per R1). When the fallback is disabled, when no entry
  of the list has an applicable rule, or when action budget or remaining time
  forbids every entry, nothing is sent and the outcome records `fallback:
  skipped:<reason>` with reason ∈ {`disabled`, `not_authorized`,
  `budget_exhausted`, `deadline_exceeded`}. A `stale_drop` fallback never
  answers the stale content and is subject to the same conditions.

- R4: **Multimodal observations and operational attachment store.**
  `ActionObservation` gains typed `parts` (default empty): `text` parts
  (`text` ≤ `max_observation_bytes` UTF-8 bytes) and `image_ref` parts
  (`attachment_id`, `content_type` ∈ {`image/png`, `image/jpeg`}, `size`,
  `width`, `height`, `captured_at`, `provider_id`). The executor validates
  parts on every terminal observation: an `image_ref` must name an attachment
  leased to the call's `run_id` and unexpired at validation, otherwise the
  observation becomes `error` (`invalid_result`) and the provider's result is
  not fed to the model. **Observation size** is defined as the sum, over all parts, of the UTF-8
  byte length of each `text` part's `text` plus the `size` (stored bytes) of
  each `image_ref` part; envelope, `result` mapping and part metadata are
  not counted, and the `size` field is checked against the store's recorded
  object size (a mismatch is `invalid_result`). An observation whose size so
  computed exceeds `max_observation_bytes` becomes `error`
  (`observation_too_large`) and every `image_ref` it named is released.
  A provider that cannot store an image (object, run or global bound of the
  store) returns `error` (`attachment_refused`) with 0 bytes retained. An
  `image_ref` whose lease expired before the model turn that would use it
  ends the run `error` (`attachment_expired`). All attachments of a run are
  released when its terminal record is written, on every exit path; a residual
  TTL cleans what a crash left. Observations, events and traces carry
  references and dimensions only: no base64 payload, no local filesystem path.

- R5: **Four capabilities behind the same contracts, on two platforms.**
  `modules/chat_context/` provides `chat.read` (read; args `limit` 1–50;
  result `messages[]{author_id, message_id, text, observed_at}`, `observed_at`,
  `coverage{returned, retained, window_seconds, complete}` where `complete` is
  `true` only when every retained message of the channel was returned and the
  retention count bound was not reached). `modules/users/` provides
  `users.read` (read; args `limit` 1–100, optional opaque `cursor`; result
  `users[]{user_id, display_name, roles?, roles_provenance?, first_seen,
  last_seen}`, `page{returned, has_more, next_cursor}`, `freshness{observed_at,
  window_seconds}`, `coverage{kind: "observed_authors", complete: false,
  retained_users, evicted}`), over a directory bounded by `max_channels`,
  `max_users_per_channel` and `max_age_seconds`, evicting oldest `last_seen`
  first; roles appear only when the ingested event carried them with a
  provenance. `modules/capture/` provides `screen.capture` (read; args
  optional `source` naming a configured source; result `{source, content_type,
  width, height, size, captured_at}` plus one `image_ref` part) from sources of
  kind `file` (re-read at each call) or `command` (configured argv producing
  PNG/JPEG bytes on stdout within `command_timeout_seconds`, default 5),
  bounded by `max_bytes` (default 5 242 880); a source that is not readable or
  executable at `prepare` leaves the module not ready. `chat.write` is
  provided by each platform module for its destinations and declares the
  delivery capability with `text_argument: text` (R1). Every one of the four
  actions is refused without an applicable rule, provider uninvoked, and each
  action's runtime destination scope is `chat`, `chat`, `capture`, `chat`
  respectively. A fictional second platform (`tests/fixtures/modules/
  fakeplatform/`) produces normalised chat for platform `fake` and serves
  `chat.write`; the full scenario runs on it with `core/` and the brain
  unchanged.

- R6: **PC agent and WebSocket JSON proxy, one agent per brain, bus not
  distributed.** `modules/proxy/` (brain side) listens; `modules/agent_link/`
  (agent side) dials `brain_url`. Protocol v1, fixed here and in
  `docs/proxy-protocol.md`: every text frame is a JSON object with integer
  `v` = 1, string `type`, and `id`. Correlation: `hello` carries an
  agent-generated non-empty string `id` (a nonce); `welcome` answers with that
  same `id` and carries the server-issued `session_id`; from `welcome` on,
  every session-scoped frame (`ping`, `pong`, `event`, `attachment`,
  `attachment_ack`) carries `id` = `session_id`, and call-scoped frames
  (`call`, `observation`, `cancel`) carry `id` = `call_id`; `error` carries
  the `id` of the frame it answers, or `null` when none applies. Before
  `welcome` the brain accepts only `hello`; any other frame received before
  pairing, or a session-scoped frame whose `id` differs from the current
  `session_id`, is answered `error unknown_session` and not processed. Frame types: `hello` (agent→brain: `agent_id`,
  `token`, `actions[]` as ActionSpec-shaped declarations, `max_frame_bytes`),
  `welcome` (brain→agent: `session_id`, accepted action names, brain limits),
  `error` (`code`, sanitised `message`, `retryable`), `call` (brain→agent: the
  ActionCall fields plus `seq` — a positive integer the brain assigns per
  session, strictly increasing from 1 with the first `call` after `welcome`
  — `deadline_utc` ISO-8601, `remaining_ms` and `max_attachment_bytes`), `attachment` (agent→brain header: `attachment_id`,
  `content_type`, `size`; followed by exactly one binary frame of `size`
  bytes), `attachment_ack` (brain→agent: `accepted` or `code` ∈
  {`attachment_too_large`, `store_full`, `unexpected_binary`}), `observation`
  (agent→brain: ActionObservation fields; `image_ref` parts name attachments
  already acknowledged), `cancel` (brain→agent), `event` (agent→brain:
  `event_type`, `payload`), `ping`/`pong`. Rules: pairing token compared in
  constant time; invalid token → `error auth_failed`, close code 4401;
  second authenticated agent while one is connected → `error agent_limit`,
  close 4409; `v` ≠ 1 → `error unsupported_protocol_version`, close 4400;
  text frame above `max_frame_bytes` (default 1 048 576) or binary frame above
  `min(store max_object_bytes, max_attachment_bytes)` → `error
  frame_too_large`, close 4413; unknown `type` → `error unknown_frame`,
  connection kept. Non-loopback `listen.host` without `tls`, or a non-loopback
  `brain_url` not using `wss`, is refused at validation. The agent executes a
  `call` through its own default-deny executor with the forwarded principal
  (peer authentication grants nothing), bounded by `min(remaining_ms, its
  action timeout)` on its monotonic clock — `deadline_utc` is informational
  and never extends a budget. **Call identity and de-duplication** (bounded state: one integer
  plus the call table): the agent keeps, per session, `last_seq` (the highest
  `seq` accepted, 0 after `welcome`) and a call table of at most
  `max_entries` (1024) retained `(call_id → observation)` entries, each
  evicted after `ttl_seconds` (300) or when the oldest is displaced by a
  newer entry; both are reset on every `welcome`. A `call` with
  `seq > last_seq` is new: it is executed and `last_seq` becomes its `seq`.
  A `call` with `seq ≤ last_seq` whose `call_id` is retained returns the
  retained observation without executing; one with `seq ≤ last_seq` whose
  `call_id` is not retained (evicted, or never seen) is refused `error
  duplicate_call_unknown` (`retryable: false`) without executing. A `call`
  whose `seq` is missing, not a positive integer, or whose `call_id` is
  retained under a different `seq`, is refused `error invalid_frame`. The brain's proxy binds itself at
  `prepare` as provider of every action in its `actions` allowlist, spec taken
  from the discovered catalog, and is not ready until a `hello` declares an
  identical spec (name, version, schemas, nature, destinations); a differing
  declaration excludes that action (`error action_mismatch`) and leaves the
  others. Disconnection marks the proxy's actions not ready; every in-flight
  call resolves `external_unknown` (cause `proxy_disconnected`) for a `write`
  action and `error proxy_disconnected` (`retryable: true`) for a `read`;
  nothing is retransmitted on reconnect, and an `observation` for a terminal
  or unknown call is ignored and counted (`late_observations`). Cancellation
  of a run or deadline expiry sends `cancel` best-effort; the executor's own
  classification stands. Heartbeat `ping` every `heartbeat_seconds` (15);
  no `pong` within `heartbeat_timeout_seconds` (10) is a drop. Reconnection
  follows decision 9. `event` frames are accepted by a source adapter only for
  `event_type` ∈ {`agent.status`}, payload validated, `metadata.source =
  proxy`, `metadata.provider_id = agent_id` set by the adapter, then published
  on the local bus; supervision trace types and `channel.*` types are refused
  (`error event_not_allowed`). No bus subscription crosses the wire.

- R7: **Two distinct deployment profiles on one `run()`.** `config.yaml.example`
  (PC): brain, platforms/sinks, `chat_context`, `users`, `capture` (local),
  `audit`. `config.server.yaml.example` (server): brain, platforms/sinks,
  `chat_context`, `users`, `proxy`, `audit`, no `capture`.
  `agent.yaml.example` (PC agent): `capture`, `agent_link`, with an agent-side
  rule granting `screen.capture` to principal `brain`. All three run with
  `python -m core.main --config <file>`; each carries the acted defaults of
  R3, explicit grants for exactly its **provided-action set**, `${NAME}`
  references and 0 literal secrets; the two brain profiles carry the
  `delivery` group of R1 configured as `mode: fixed` with the single entry
  `{action: chat.write, text_argument: text}` and no `overrides` (decision
  1), so their observable delivery is one `chat.write` per run. The
  provided-action set of a profile is the union of (a) the actions declared
  by the manifests of its enabled modules and (b) the actions named by the
  `actions` allowlist of an enabled `proxy` module (remote-bound, spec taken
  from the catalog per decision 7); the proxy manifest itself declares no
  action. Hence: PC profile
  {`chat.read`, `users.read`, `screen.capture`, `chat.write`} all from (a);
  server profile the same four names, `screen.capture` from (b) only; agent
  profile {`screen.capture`} from (a). The brain code has no
  capture branch and no transport branch: it calls the executor; whether
  `screen.capture` is served by `capture` or by `proxy` is decided by which
  module binds it, and a profile enabling both for overlapping destinations
  fails preparation with the ambiguity diagnostic. `--check-config` validates a
  profile (configuration, enabled manifests, module settings hooks) without
  opening any transport and exits 0, or 2 with diagnostics naming module and
  field, never a value.

- R8: **Installable, discoverable, hygienic.** `pyproject.toml` ships `core*`
  and `modules*` with every `module.yaml`, and the console script
  `twitch-ia-compagnon`; `modules_directory: builtin` resolves to the installed
  `modules` package so a clean environment discovers the 8 shipped manifests
  (`twitch`, `brain`, `audit`, `chat_context`, `users`, `capture`, `proxy`,
  `agent_link`). The PC/server topology trial is recorded in `docs/README.md`
  (commit, profiles, hosts or loopback, TLS or not, outcome, limits) before
  the topology is described as delivered. The test suite contains no
  positive-duration `asyncio.sleep`/`time.sleep`, and `core/` and `modules/`
  contain no model-name literal; both are asserted by a grep over the tree.

## Acceptance criteria

- AC1 (R1): With grants for `chat.read`, `users.read`, `screen.capture` and
  `chat.write` on the served channel and a scripted model answering
  [`chat.read` proposal, `screen.capture` proposal, final response], one
  admitted twitch message yields exactly 3 executor calls with call ids
  `<run_id>/call-1`, `/call-2`, `/call-3`, exactly 1 send at the fake transport
  (the final text), 0 sends before it, `brain.run.completed` with
  `status: success`, `turns: 3`, `action_calls: 3`, and every trace of the
  scenario sharing one `run_id` and one `conversation_id`.
- AC2 (R1): In AC1 the second model request contains the `chat.read`
  observation's messages as text and the third contains exactly one image
  input derived from the capture; the store's usage for the run is 0 objects
  after `brain.run.completed`.
- AC3 (R1): A scripted response carrying 2 tool calls ends the run `error`
  with `failure: unsupported_response_shape`, 0 executor calls; the same
  holds for a tool call accompanied by text content.
- AC4 (R1): A proposal naming `screen.capture` with no applicable rule returns
  a `refused` observation to the model (provider invoked 0 times) and the run
  continues: with a scripted final response next, the run ends `success` with
  `turns: 2`.
- AC5 (R1): A proposal whose arguments are the string `"oops"` yields one
  transcript observation with `error.code == "malformed_arguments"`, 0
  executor calls, and counts as 1 turn.
- AC6 (R1): Every model request in AC1 lists as tools exactly the authorized
  read actions (`chat.read`, `users.read`, `screen.capture`) and never
  `chat.write`; the system instructions contain no `[send:` tag and no tool
  named `chat.write`.
- AC46 (R1): With a `chat.write` grant for principal `brain` on the served
  channel, a scripted model proposing `chat.write` (`{"text": "hi"}`) on turn
  1 and a final response on turn 2 yields one transcript observation with
  `status == "refused"` and `error.code == "not_a_read_action"`, 0 executor
  calls for that proposal, exactly 1 send at the fake transport (the final
  text, as `<run_id>/call-1`), and `brain.run.completed` with
  `status: success`, `turns: 2`, `action_calls: 1`; a proposal naming
  `nope.action` yields `refused` with `error.code == "unknown_action"`, 0
  executor calls.
- AC7 (R1): Only the final response text is written to conversation memory,
  and only for a `success` delivery; a `FAIL_AFTER_EMISSION` delivery leaves
  memory unchanged and the record's `delivery == "external_unknown"`.
- AC49 (R1): With the `delivery` group of both example brain profiles
  (`mode: fixed`, `actions: [{action: chat.write, text_argument: text}]`,
  no `overrides`), the AC1, AC4, AC7, AC13, AC18, AC31, AC33 and AC46
  scenarios produce exactly the executor calls, call ids, sends, memory
  writes, `fallback` and `delivery` values those criteria state; in AC1 the
  record's `deliveries` is exactly `[{action: "chat.write", call_id:
  "<run_id>/call-3", text: true, status: "success"}]`; and the fixed entry
  `{action: chat.write}` without `text_argument` resolves to the same list
  (mapping inherited from the declaration) with the same results.
- AC50 (R1): With the fixture delivery module enabled and `mode: fixed`,
  `actions: [{action: chat.write, text_argument: text}, {action:
  stream.set_scene, arguments: {scene: "answering"}}]`, a scripted model
  answering a final response T on turn 1 yields exactly 2 executor calls, in
  order: `chat.write` as `<run_id>/call-1` with arguments `{"text": T}`, then
  `stream.set_scene` as `<run_id>/call-2` with arguments exactly `{"scene":
  "answering"}` (T present under no key); 0 executor calls before the final
  response, exactly 1 send at the chat transport, the scene provider invoked
  exactly once, `deliveries` of length 2 with `text` `[true, false]` and both
  `status == "success"`, `delivery == "success"`, `action_calls: 2`, and
  memory written once with T. The same two entries in the reverse order
  yield the reverse call ids.
- AC51 (R1): With `actions: [{action: chat.write, text_argument: text},
  {action: audio.say, text_argument: text}]`, the chat transport's send and
  the audio provider's `text` argument both equal T, `deliveries` reports
  `text` `[true, true]`, and every model request of the run lists as tools
  exactly the authorized read actions (as AC6) — neither `chat.write` nor
  `audio.say` is offered.
- AC52 (R1): With `mode: modules`, `preference: [audio.say, chat.write]`,
  the fake platform and the fixture delivery module enabled, the
  `brain.delivery.resolved` trace lists `[audio.say, chat.write,
  stream.set_scene]` (`overlay.raw` absent: no delivery capability) and a
  final response yields 3 executor calls in that order, T passed to the
  first two and to no argument of the third; with `preference:
  [stream.set_scene]` the resolved list starts with `stream.set_scene` and
  continues with the other two in catalog order; with `preference:
  [audio.say, chat.write, nope.action]` the resolved list is unchanged and
  `nope.action` is listed as ignored in the trace; with the fixture delivery
  module not enabled and the same settings the resolved list is
  `[chat.write]` and the run matches AC49.
- AC53 (R1): Each of the fixed lists `[{action: nope.action}]`, `[{action:
  chat.read, text_argument: text}]`, `[{action: overlay.raw, text_argument:
  text}]`, `[{action: stream.set_scene, text_argument: text}]`, `[{action:
  chat.write, text_argument: body}]`, `[{action: chat.write, text_argument:
  text, arguments: {text: "x"}}]` and `[]` makes `prepare` fail before the
  readiness barrier with one diagnostic naming `delivery.actions[0]`
  (respectively `delivery`) and reason `unknown_action`, `not_a_delivery`,
  `not_a_delivery`, `text_mapping_missing`, `text_mapping_missing`,
  `text_mapping_ambiguous`, `empty`; `mode: modules` with no enabled
  delivery-capable action fails with reason `empty`; in every case 0
  scenario requests are sent, 0 sends occur and the brain is not ready.
  `validate_settings` refuses `delivery.mode: teleport` and a `fixed` list
  whose entry lacks `action`, each with one diagnostic naming the field, and
  `--check-config` exits 2 on them.
- AC54 (R1): With the AC50 list, a chat provider scripted `FAIL_AFTER_EMISSION`
  yields `deliveries[0].status == "external_unknown"`,
  `deliveries[1].status == "success"` with the scene provider invoked exactly
  once, `delivery == "external_unknown"` and memory unchanged; with the chat
  send succeeding and the scene provider raising, `deliveries` statuses are
  `["success", "error"]`, `delivery == "error"` and memory is written once
  with T; with no applicable rule for `stream.set_scene`, its entry is
  `refused`, its provider is invoked 0 times and the chat send is still
  made.
- AC55 (R3): AC13 run with the AC50 list delivers `fallback.text` by exactly
  1 chat send and invokes the scene provider exactly once with `{"scene":
  "answering"}`, `deliveries` has 2 entries both `success`, `fallback ==
  "sent"`, `delivery == "fallback:success"`; with `max_action_calls` leaving
  exactly 1 call at the terminal step, `deliveries` statuses are
  `["success", "skipped:budget_exhausted"]` and `delivery ==
  "fallback:skipped:budget_exhausted"`; with no applicable rule for either
  entry, 0 sends, 0 provider invocations and `fallback ==
  "skipped:not_authorized"`.
- AC56 (R1): `grep -rn` over `modules/brain/` for `chat.write`, `audio.say`,
  `stream.set_scene`, `fakeeffects` and `twitch` matches 0 lines; the
  AC50–AC55 scenarios run the shipped `modules/brain/` with no monkeypatch,
  subclass or brain setting other than `delivery` changed relative to AC49;
  and a `mode: modules` profile taken from the fixture delivery module
  disabled to enabled changes the resolved list from `[chat.write]` to the
  3-entry list of AC52 with 0 files changed under `core/` and `modules/`.
- AC57 (R1): With `overrides: {"fake/chan-b": {mode: fixed, actions:
  [{action: stream.set_scene, arguments: {scene: "b"}}]}}` over the AC49
  default, a run on `fake/chan-b` invokes only `stream.set_scene` (0 sends,
  `deliveries` of length 1, `text: false`) and a run on `fake/chan-a` sends
  exactly one chat message; an override naming `nope.action` fails
  preparation with a diagnostic naming
  `delivery.overrides["fake/chan-b"].actions[0]` and reason `unknown_action`.
- AC58 (R7): Both example brain profiles carry `modules.brain.delivery` equal
  to `{mode: fixed, actions: [{action: chat.write, text_argument: text}]}`
  and no `overrides`; the twitch manifest and the fake platform manifest
  declare `chat.write` with `delivery: {text_argument: text}`; and a manifest
  declaring `delivery` on an action of nature `read` is refused at manifest
  validation with a diagnostic naming the action.
- AC8 (R2): With `capabilities.required: [structured_output, vision]`, a fake
  backend answering the first probe with one tool call and the second with a
  non-2xx status makes `prepare` fail; the startup diagnostic equals
  `module 'brain': backend capability 'vision' not verified: non_success_status`
  and contains neither the api key nor the endpoint; 0 scenario requests are
  sent. With both probes answered correctly the module is ready and exactly 2
  probe requests were made, the second containing exactly one image part.
- AC9 (R2): A probe answered with plain text (no tool call) refuses
  `structured_output` with reason `no_tool_call`; with 2 tool calls, reason
  `multiple_tool_calls`; with `capabilities.required: [structured_output]`
  exactly 1 probe request is made.
- AC10 (R2): `validate_settings` refuses `capabilities.required: [vision]`
  (missing `structured_output`) and `[structured_output, audio]` (unknown),
  each with one diagnostic naming `capabilities.required`.
- AC11 (R2): With `capabilities.required: [structured_output]` only, an
  `image_ref` observation ends the run `error` with `failure:
  capability_missing`, `capability: vision`, 0 further model requests, and
  the attachment released.
- AC12 (R2): The image bytes appear in exactly one place: the model request
  body; no bus event, no supervision trace and no audit record of the run
  contains a base64 payload or a filesystem path, and traces carry the
  `attachment_id`, `size`, `width` and `height` only.
- AC13 (R3): With `model_turns: 2` and a model proposing `chat.read` on every
  turn, the run ends `error`, `failure: budget_exhausted`, `budget:
  model_turns`, after exactly 2 model requests, and the fallback text is
  delivered by exactly 1 `chat.write` call (`delivery == "fallback:success"`,
  `fallback == "sent"`).
- AC14 (R3): With `max_repeated_actions: 2`, the third identical
  `chat.read` proposal (same arguments) is not executed: 2 executor calls for
  it, run `error`, `budget: max_repeated_actions`; a third `chat.read` with a
  different `limit` is executed.
- AC15 (R3): With `max_tokens: 200` and a backend reporting `usage.total_tokens:
  250` on the first turn, the run ends `error`, `budget: max_tokens`,
  `tokens: 250`, `tokens_estimated: false`; without `usage` the recorded
  `tokens_estimated` is `true`.
- AC16 (R3): Advancing the injected clock 31 s while a model request is held
  ends the run `timeout` with exactly 1 model request; advancing 11 s while a
  `screen.capture` call is held yields a `timeout` observation fed to the model
  and, with a scripted final response next, `success`.
- AC17 (R3): With `total_run_seconds: 60` and `delivery_reserve_seconds: 10`,
  a run whose clock reaches 51 s after admission with the model proposing
  another action makes 0 further model requests and delivers the fallback;
  at 60 s or later nothing is sent, `fallback == "skipped:deadline_exceeded"`.
- AC18 (R3): With `fallback.enabled: false`, AC13's run sends 0 messages and
  records `fallback == "skipped:disabled"`; with the fallback enabled but no
  `chat.write` rule, 0 messages and `skipped:not_authorized`, provider
  invoked 0 times; with `max_action_calls` already consumed, 0 messages and
  `skipped:budget_exhausted`.
- AC19 (R3): A queued work expiring `wait_seconds` produces `stale_drop`, 0
  model requests and exactly 1 fallback send whose text is `fallback.text`;
  the same with `fallback.enabled: false` produces 0 sends.
- AC20 (R3): `validate_settings` refuses `admission.wait_seconds` greater than
  `admission.total_run_seconds`, and each of the 10 budget/admission values
  when absent, zero, negative or infinite, one diagnostic per field naming it.
- AC21 (R4): `ActionObservation(parts=...)` refuses a part of unknown type,
  a `text` part above `max_observation_bytes`, and an `image_ref` missing any
  of its 7 fields, each with a `ContractError` naming the field; an
  observation with no `parts` argument constructs as before.
- AC47 (R4): With `max_observation_bytes: 5242880`, an observation carrying
  one `text` part of 3 145 728 bytes and one `image_ref` part of `size`
  3 145 728 (each alone under the bound, 6 291 456 in total) becomes `error
  observation_too_large`, the store reports 0 objects for the run before the
  next model request, and that request contains 0 image parts; the same two
  parts with the image at `size` 2 097 152 (5 242 880 in total) are accepted
  and the next request contains exactly 1 image part; an `image_ref` whose
  `size` differs from the stored object size by 1 byte is `invalid_result`.
- AC22 (R4): A provider returning an `image_ref` for an attachment leased to
  another run, or already expired on the injected clock, yields an
  `invalid_result` error observation and the model request that follows
  contains 0 image parts.
- AC23 (R4): With `attachments.max_object_bytes: 1024`, a capture producing
  2048 bytes returns `error attachment_refused`, the store retains 0 bytes
  for the run, and the model receives the error observation.
- AC24 (R4): An `image_ref` leased at t and used by a model turn at
  t + `ttl_seconds` + 1 (clock advanced) ends the run `error
  attachment_expired` with 0 model requests carrying the image.
- AC25 (R4): For each terminal path — success, error, timeout, cancellation
  during a capture, cancellation during the model call, cancellation during
  delivery — the store reports 0 objects for the run once `brain.run.completed`
  is published, and `SupervisedTasks.active` returns to its pre-run value.
- AC26 (R5): `chat.read` with `limit: 3` on a channel holding 5 retained
  messages returns the 3 newest oldest-first with `coverage.returned == 3`,
  `retained == 5`, `complete == false`; with `limit: 10` it returns 5 with
  `complete == true`; on a channel at its `max_messages` bound `complete`
  is `false`; `limit: 0` and `limit: 51` are `invalid_arguments`.
- AC27 (R5): After 150 distinct authors spoke on one channel with
  `max_users_per_channel: 100`, `users.read` `limit: 40` pages return 40, 40
  and 20 users with `has_more` true, true, false, no user repeated, every
  result's `coverage.complete == false`, `coverage.evicted == 50`; a
  `cursor` from another channel is `invalid_arguments`.
- AC28 (R5): A user whose last message is older than `max_age_seconds` on the
  injected clock is absent from `users.read`; `roles` is present only for a
  user whose event carried `author.roles` with `author.roles_provenance`, and
  absent (not empty) otherwise; `freshness.observed_at` equals the injected
  clock at read.
- AC29 (R5): `screen.capture` from a `file` source returns one `image_ref`
  part whose `width`, `height` and `size` match the file, and a `command`
  source whose process exceeds `command_timeout_seconds` returns `error
  capture_timed_out` with 0 bytes stored; a source missing at `prepare`
  leaves the module not ready and `module.degraded` names the source.
- AC30 (R5): Each of the four actions called without an applicable rule
  returns `refused` with `not_authorized` and the provider is invoked 0
  times, on twitch and on the fake platform.
- AC31 (R5): The AC1 scenario run entirely on platform `fake` (fake chat
  event, `chat.read`, `screen.capture`, final response, fake `chat.write`)
  produces the same call sequence and trace set as on twitch, and `grep -r
  twitch core/ modules/brain modules/chat_context modules/users
  modules/capture modules/proxy modules/agent_link` matches 0 lines.
- AC32 (R6): Over an in-memory transport, `hello` with a wrong token receives
  `error auth_failed` and close 4401 and 0 actions become ready; a second
  agent with the right token receives `error agent_limit`, close 4409, while
  the first stays connected; a frame with `v: 2` receives
  `unsupported_protocol_version`, close 4400; a 1 048 577-byte text frame
  receives `frame_too_large`, close 4413.
- AC48 (R6): A `hello` with `id: "h1"` and a valid token receives a
  `welcome` with `id == "h1"` and a non-empty string `session_id`; a `ping`
  sent before any `hello` receives `error unknown_session` (`id: null`) and
  the connection stays open; after `welcome`, a `ping` with `id ==
  session_id` receives a `pong` with the same `id`, and a `ping` with `id:
  "other"` receives `error unknown_session` and 0 `pong`; a `hello` with a
  missing or empty `id` receives `error invalid_frame`, close 4400.
- AC33 (R6): The AC1 scenario with `screen.capture` served through the proxy
  (agent side in the same test process behind the in-memory transport)
  produces the same brain-side call sequence, traces and store cleanup as the
  local provider, and the image reaches the model from the brain's store
  after exactly one `attachment` header + one binary frame + one
  `attachment_ack`; the `observation` frame contains no filesystem path.
- AC34 (R6): A `call` with `remaining_ms: 5000` received by an agent whose
  wall clock is 3 h ahead of the brain's is bounded at 5 s on the agent's
  monotonic clock (`deadline_utc` ignored for the bound); with
  `remaining_ms: 20000` and agent `action_seconds: 10`, the bound is 10 s.
- AC35 (R6): Dropping the connection while a `write` call is in flight
  resolves it `external_unknown` (cause `proxy_disconnected`) with 0
  retransmissions; while a `read` call is in flight, `error
  proxy_disconnected` with `retryable: true`; the run continues with the
  observation, and the proxy's actions are absent from the next authorized
  view until `hello`/`welcome` succeed again.
- AC36 (R6): An `observation` frame for a call already terminal, or a second
  `observation` for the same `call_id`, changes nothing and increments
  `late_observations` by 1 each; a `call` frame repeating a retained
  `call_id` and `seq` executes 0 additional times and returns the retained
  observation; the same frame re-sent `ttl_seconds` + 1 later on the
  injected clock returns `duplicate_call_unknown` with 0 executions; a
  never-seen `call_id` with `seq` ≤ `last_seq` returns
  `duplicate_call_unknown` with 0 executions; with `max_entries: 2`, three
  distinct calls then a repeat of the first return `duplicate_call_unknown`
  and the table holds exactly 2 entries; after a new `welcome` a `call` with
  `seq: 1` executes.
- AC37 (R6): With an injected RNG returning 0.5 and an injected sleeper, the
  agent's reconnection delays after 6 consecutive failures are exactly
  [0.5, 1.0, 2.0, 4.0, 8.0, 15.0] s (full jitter of 1·2ⁿ capped at 30), and
  after a `welcome` the next failure starts again from the 1 s base.
- AC38 (R6): An `event` frame with `event_type: agent.status` is published on
  the local bus with `metadata.source == "proxy"` and `metadata.provider_id`
  equal to the agent id, whatever metadata the frame carried; `event_type:
  brain.run.completed` and `channel.chat.message` receive `event_not_allowed`
  and publish nothing; no `subscribe` call crosses the transport.
- AC39 (R6): Two real processes on loopback (brain with the server profile
  and a fake model, agent with the agent profile): a valid token pairs, an
  invalid one is closed 4401; the AC1 scenario completes end to end with the
  image transferred by reference; killing the agent process and restarting it
  pairs again and `screen.capture` is ready again; a `screen.capture` call in
  flight at the kill resolves `error proxy_disconnected`. The test waits on
  events and process exits only, with bounded `wait_for`, never on a sleep.
- AC40 (R7): `--check-config` exits 0 on each of the three example files with
  dummy environment values and opens 0 sockets; with `TWITCH_ACCESS_TOKEN`
  unset it exits 2 and the diagnostic names the setting path and no value.
- AC41 (R7): Enabling `capture` and `proxy` with `actions: [screen.capture]`
  in one brain profile fails preparation with the ambiguity diagnostic naming
  `screen.capture` and both providers; with only one enabled the model is
  offered `screen.capture` and the brain code path is identical (0 references
  to `capture` or `proxy` in `modules/brain/`).
- AC42 (R7): Each example file contains 0 literal credentials (every
  credential setting is a `${NAME}` reference); the set of action names its
  authorization rules grant equals its provided-action set as defined in R7 —
  PC and server: exactly {`chat.read`, `users.read`, `screen.capture`,
  `chat.write`} (4 names; in the server file `screen.capture` appears in
  `modules.proxy.actions` and in no enabled manifest), agent: exactly
  {`screen.capture`} (1 name); the brain budgets of the two brain profiles
  equal R3's acted defaults (5, 30, 10, 8192, 5242880, 6, 2, 10, 30, 60), and
  every module-owned settings validator accepts its section.
- AC43 (R8): Installing the built distribution with `pip install --no-deps`
  into an empty target directory and running, from a directory outside the
  checkout, a subprocess that resolves `modules_directory: builtin` discovers
  exactly 8 manifests named `twitch, brain, audit, chat_context, users,
  capture, proxy, agent_link`, and `twitch-ia-compagnon --help` exits 0. The
  test skips only when `pip` is unavailable, naming that reason.
- AC44 (R8): `docs/README.md` contains a section "Phase 1 topology trial"
  naming the commit, the two profiles used, whether hosts were distinct or
  loopback, whether TLS was used, the observed outcome and the remaining
  limits; a test asserts the section and its 6 fields exist.
- AC45 (R8): A test greps `tests/` for `asyncio.sleep(` and `time.sleep(`
  with any argument other than a literal `0`/`0.0` and finds 0 matches, and
  greps `core/` and `modules/` for the literals `gpt-`, `claude-`, `llama`,
  `mistral`, `gemini` and finds 0 matches.

## Caller enumeration

Search method: `grep -rn` over `core/`, `modules/`, `tests/` for each symbol
and for the settings keys; dynamic dispatch through the registry's
`provider.invoke` is found by grepping `invoke(`. Blind spot: YAML settings
consumed by name inside tests (`VALID_SETTINGS`, `SETTINGS` constants) were
located by grepping the key names, not by type.

### `ActionObservation` gains optional `parts` (default `()`)

| File | Function/Method | Migration note |
|------|----------------|----------------|
| core/actions.py | `ActionExecutor.invoke` (observation built at ~L1417) | Validate `parts` of provider observations; synthetic executor observations carry no parts. |
| modules/brain/__init__.py | `_failed_delivery`, `run`, `_deliver` | Read parts for transcript rendering; delivery observations unchanged (`_deliver` iterates the resolved delivery list, one observation per entry). |
| modules/twitch/__init__.py | `_invoke_chat_write` (2 constructors) | No change: default parts. |
| tests/conftest.py | `FakeSendProvider.invoke` (2 constructors) | No change: default parts. |
| tests/test_actions.py | observation doubles | No change; add parts-validation cases (AC21, AC22). |
| tests/test_brain.py, tests/test_lifecycle.py, tests/test_retention.py | provider doubles constructing observations | No change: default parts. |
| modules/chat_context, modules/users, modules/capture, modules/proxy, modules/agent_link (new) | providers | Construct observations with `text`/`image_ref` parts per R4/R5. |

### `AdmissionScheduler.__init__` gains optional stale-drop hook

| File | Function/Method | Migration note |
|------|----------------|----------------|
| modules/brain/__init__.py | `BrainModule.__init__` / `prepare` (owned scheduler) | Pass the fallback hook (R3, AC19). |
| tests/test_admission.py | 4 constructors | No change (hook optional); add the hook contract test. |
| tests/test_brain.py | shared-scheduler harness (`RecordingScheduler`) | The forwarder that binds `run` also binds the stale-drop hook. |

### Brain settings schema (`budget.*`, `fallback`, `capabilities`, `delivery`) and model request shape

| File | Function/Method | Migration note |
|------|----------------|----------------|
| modules/brain/__init__.py | `validate_settings`, `_OWNED_LIMITS`, `_Settings.from_mapping`, `_request_model`, `_compose`, `run` | Extended per R1–R3. |
| modules/brain/__init__.py | `DELIVERY_ACTION` constant, `prepare`, `_deliver`, `_failed_delivery` | The hardcoded `chat.write` constant is removed; `prepare` resolves the `delivery` group against the catalog (R1); `_deliver` and the fallback invoke the resolved list, one call per entry. |
| modules/brain/module.yaml | `settings_schema.properties.budget/fallback/capabilities/delivery` | Add keys; `required` lists them. |
| config.yaml.example, config.server.yaml.example | `modules.brain` | Carry the new keys with acted defaults and the single-entry `delivery` group. |
| tests/test_brain.py | `VALID_SETTINGS`, `SETTINGS`, `activate_with`, `Harness.requests/prompt` | Constants carry the new keys (`delivery` included, single entry `chat.write`); harness answers the probe and excludes probe requests from `requests()`; the test-side `DELIVERY_ACTION` constant stays a test constant. |
| tests/test_examples.py | `test_example_config_satisfies_every_module_owned_settings_validator` | Reads the new keys through the validator; no assertion change. |
| tests/test_integration.py, tests/test_shutdown.py, tests/test_retention.py, tests/test_lifecycle.py, tests/test_main.py | brain settings fixtures and `completion()` scripts | Settings fixtures gain the new keys; probe answered by the shared `FakeSession`. |

### Action declaration gains optional `delivery` capability

| File | Function/Method | Migration note |
|------|----------------|----------------|
| core/contracts.py | `ActionSpec` / manifest action validation | Accept the optional `delivery: {text_argument}` field on non-`read` actions; the field takes part in spec equality. Existing declarations without it are unchanged. |
| modules/twitch/module.yaml, tests/fixtures/modules/fakeplatform/module.yaml | `chat.write` declaration | Add `delivery: {text_argument: text}`; nothing else changes. |
| modules/proxy/__init__.py | `hello` spec comparison | No code branch: the compared spec now includes the field. |
| tests/test_examples.py | `test_manifests_are_unique_and_have_coherent_capabilities` (`EXPECTED_MANIFESTS` table) | Table rows for `twitch` and the new fixture carry the delivery declaration; no assertion weakened. |
| tests/fixtures/modules/fakeeffects (new) | manifest and providers | Declare `audio.say`, `stream.set_scene` (delivery) and `overlay.raw` (no delivery). |

### `core.main.load_config` / `run` / `main` (`builtin`, `--check-config`)

| File | Function/Method | Migration note |
|------|----------------|----------------|
| core/main.py | `main`, `run`, `load_config` | Additive flag and reserved value; existing calls unchanged. |
| tests/test_main.py | `run(...)` / `main([...])` callers | No change; add `--check-config` and `builtin` cases (AC40, AC43). |
| tests/test_integration.py, tests/test_shutdown.py, tests/test_lifecycle.py, tests/test_retention.py | `run(config_path, stop_event, ...)` | No change. |

### Shared test double `FakeSession` (probe-aware)

| File | Function/Method | Migration note |
|------|----------------|----------------|
| tests/conftest.py | `FakeSession.post`, `HeldSession.post`, `completion` | Answer a probe request without consuming scripted results; record probes separately; add `tool_call(...)` script helper. |
| tests/test_brain.py, tests/test_integration.py, tests/test_shutdown.py, tests/test_retention.py, tests/test_lifecycle.py, tests/test_main.py | users of `FakeSession`/`completion` | Per-run request assertions keep their meaning (probe excluded). |

### Harness adaptations that are **not** allowlisted

The following existing assertions keep their guarantee and pass once the
harness is adapted as above; they are listed so the plan budgets the work:
`tests/test_brain.py` assertions `len(harness.requests()) == 1` and
`harness.requests() == []` (per-run scenario requests; the probe is a
per-activation request recorded separately), `send["call_id"] ==
f"{record.run_id}/call-1"` (a plain final response with no action is still
call 1), `tests/test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant`
(compares the schema to the test's own `VALID_SETTINGS` constant, which gains
the new keys, `delivery` included; `"actions" not in manifest` stays true), and
`tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities`
(iterates the test's `MODULE_NAMES`/`EXPECTED_MANIFESTS` tables, extended to
the new manifests; its "only the chat input declares an action" docstring is
updated, no assertion weakened), and
`tests/test_examples.py::test_example_trigger_policies_give_the_two_channels_two_distinct_policies`
(its `_activate_example` helper points the transport seams of the enabled
modules at fakes; it gains seams for the new modules — an injected capture
source, the probe-aware model session — and its assertions are unchanged).

## Test failure allowlist

- `tests/test_brain.py::test_admitted_message_drives_one_configured_request_with_viewer_context` — asserts `DELIVERY_ACTION in system` and `CHAT_WRITE_SPEC.description in system`; R1/AC6 forbid offering `chat.write` to the model: delivery is the runtime's act, authorized read actions are offered as tools.
- `tests/test_brain.py::test_request_offers_no_action_without_a_grant_and_reads_the_view_afresh` — asserts `DELIVERY_ACTION in harness.prompt(1)[0]["content"]` after granting `chat.write`; R1/AC6: a `chat.write` grant changes deliverability, never the offered tools (the per-turn re-read of the authorized view is re-asserted with a `chat.read` grant instead).
- `tests/test_examples.py::test_example_config_is_complete_and_contains_no_literal_credentials` — asserts `enabled_modules == ["twitch", "brain", "audit"]`; R7 makes `config.yaml.example` the PC profile enabling six modules.
- `tests/test_examples.py::test_example_authorization_grants_exactly_the_actions_the_modules_declare` — asserts `declared == {"chat.write"}` and that the example's rules name exactly that set; R5/R7/AC42: the PC profile declares and grants `chat.read`, `users.read`, `screen.capture`, `chat.write`; the assertion's "declared" set is redefined as R7's provided-action set (enabled manifests ∪ proxy `actions` allowlist) so the server profile is checked by the same rule.
