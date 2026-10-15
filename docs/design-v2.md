# Design v2 — Multi-platform agentic companion

**English** · [Français](design-v2.fr.md)

Date: 9 September 2026. Status: **target blueprint, to be implemented**.
Starting point: `66d9a7e`, reference suite of 189 tests.

This document fixes the product direction and the architecture contracts. It delivers no feature.
**Existing** designates the code at that SHA; **target** designates the decisions to realise in
phases 0–2; **later** designates the conditional extensions of phase 3+. The new contract names,
paths and parameters below are normative proposals for the implementation, not APIs already
available.

Authority: the September 2026 user decisions prevail over the still-open questions of the
08-09-2026 review (`/tmp/twitchia-arch-review.md`, sections A, B1–B8 and C.1–C.5). In particular,
multi-platform, the agentic loop, local/remote deployment, capture delegated to modules and
per-input configurable triggering are **settled**. The review remains an external working source,
not needed to understand this file. The [plan](../plan.md), the [MVP spec](../spec.md) and the
[documentation index](README.md) keep their historical role; their guarantees do not automatically
become those of v2.

## 1. Vision and principles

The companion is ultra-adaptable: platforms, channels, context sources, model, outputs,
permissions, limits and topology are configurable. Twitch is one platform module among others. The
repository's name does not limit the product to Twitch or to text.

The **brain is a multi-turn run engine**: the model may request a chat read, observe a capture,
then decide to answer. The input/output modules are **tool** providers, named **actions** in the
runtime contract, reads included. The model proposes a structured action; an executor checks it and
calls its provider; a text and/or image observation returns to the model. This semantics requires no
dependency on OpenAgents or on a skills framework.

Responsibilities are stable:

- The **core** owns the contracts, the registry, the shared services, the per-input trigger
  evaluation engine and the lifecycle coordination.
- The **brain** owns admission, per-session scheduling, run tasks, budgets and the model's working
  conversation.
- Each **module** owns its capabilities, transports, resources and handlers; the brain knows
  neither OBS, nor OS/game capture, nor Helix.
- The **bus** carries facts, normalised inputs and internal traces. It keeps its awaitable
  ordered-chain contract; it is neither an RPC nor a proof of external delivery.
- **Configuration** selects the modules and the provider bindings, local or remote proxies, behind
  the same action contract.

The companion can run on the streamer's PC, on a server, or with a server brain and the providers on
the PC. The target includes those topologies; distributing the bus, a database and several
concurrent brain instances are not prerequisites.

```text
Platform sources → channel.chat.message → fact bus
                                                  │ short handlers
                                    chat context + per-input trigger
                                                  │
                                          bounded admission
                                                  │
                                    session queues → bounded workers
                                                  │
                              brain: model ⇄ executor ⇄ observation
                                                  │
                                       registry of ready providers
                                           /               \
                                  local handler      proxy → PC agent
                                                  │
                                  acknowledged output → run ends

Bus ← correlated health, trigger, admission, run and action facts
```

## 2. Platform-neutral vocabulary

### 2.1. Chat event and destination

The target keeps the envelope of `core/bus.py`: `type`, `payload`, `metadata`, and the existing type
`channel.chat.message`. `metadata.schema_version = 2` distinguishes the new payload. The bus stays
generic; domain contracts validate the data at their boundaries.

| Target field | Meaning and constraint |
| --- | --- |
| `payload.platform` | Stable platform identifier, for instance `twitch`; never inferred from a module's display name. |
| `payload.channel_id` | Opaque identifier of the channel on that platform. |
| `payload.author` | `id`, `display_name`, possibly `roles` and their trusted provenance. The display name is not an identity key. |
| `payload.message_id`, `payload.text` | Source identifier and bounded text of the message. Message uniqueness is carried by `(platform, channel_id, message_id)`. |
| `payload.thread_id`, `payload.parent_message_id` | Optional; explicit absence when the source does not provide them. No invented thread. |
| `metadata` | `schema_version`, `event_id`, `source`, `provider_id`, `occurred_at`, `received_at`, then whatever correlation is available. Timestamps are UTC; local ingestion order has a per-channel sequence. |

`Destination` contains `platform`, `channel_id`, possibly `thread_id` and `parent_message_id`. The
sending identity comes from the configured binding and the provider's permissions; the model does
not freely choose an authenticated account. A destination outside the perimeter is refused.

Input compatibility translates the current Twitch fields `broadcaster_id`, `chatter_id`,
`chatter_name`, `message_id`, `text` into this shape; `metadata.source = twitch` is still kept. The
translation happens at the boundary, once, with an explicitly known platform. Old events are not
rewritten in the archives.

### 2.2. Identity, session and correlation

**Settled decision — hybrid session:** `SessionKey = (platform, channel_id, viewer_id)` for runs and
conversational memory; `viewer_id` comes from the author's trusted identity (`author.id` for chat).
Any isolation by companion instance wraps that key without changing its granularity. The global chat
context separately uses `(platform, channel_id)`. Two platforms or channels never share
conversational memory, even with the same `viewer_id`; the viewers of one channel share only its
chat context. The thread stays destination information, not a session variant. Serialisation must be
collision-free, with no ambiguous concatenation.

- `conversation_id` identifies that session and its memory; its key stays stable for as long as it
  exists.
- `run_id` identifies one unit of admitted work until its termination, including queue wait and
  final delivery.
- `call_id` identifies one action invocation. It is generated by the runtime; a model backend's tool
  call identifier is kept separately when needed.
- `source_event_id` and `source_message_id` link back to the trigger; `provider_id` identifies the
  executing binding. Session identities and rights come from the runtime, not from arguments the
  model invented.

An absent identity must never make unknown viewers converge into one memory: reject admission when
the module provides no trusted viewer identity. Destination and session stay distinct: a
conversation does not authorise every output of the channel.

### 2.3. Lifecycle facts and traces

New target types, following the current dotted convention:

| Events | Emitter and useful content |
| --- | --- |
| `module.ready`, `module.degraded`, `module.stopped` | Supervision: provider/module, affected capabilities, sanitised reason, date. |
| `input.trigger.accepted`, `input.trigger.rejected` | Ingestion: source event, input/channel, policy version, bounded decision and reason; no `run_id` at that stage. |
| `brain.admission.accepted`, `brain.admission.rejected` | Admission: message, session, `run_id` when accepted, reason and queue depth. |
| `brain.run.started`, `brain.run.completed` | Brain: correlation, delays, counters, terminal state and separate delivery outcome. |
| `action.started`, `action.completed` | Executor: action, provider, `call_id`, terminal status, duration and bounded summary. |
| `channel.chat.sent` | Provider: confirmed send fact, destination and external identifier. Never triggered by a mere bus acceptance. |

These events **describe** transitions; no subscriber may have to answer in order to unblock a call.
Terminal state is recorded by its owner before its trace is published. An audit failure can neither
cancel an external effect nor trigger its repetition. A local sequence plus the IDs make causality
reconstructible without assuming a global publication order.

The audit keeps selected and redacted metadata, statuses, wait times, costs/tokens when known,
rejections, stale-message drops (`stale_drop`), timeouts and trace losses. By default it archives
neither images, nor full prompts, nor the model's private reasoning. Arguments and results are
logged only in an authorised and bounded form. Internal events and the companion's own outputs are
not run triggers.

### 2.4. Per-input configurable triggering

**Settled decision:** the streamer chooses a trigger policy **per input/channel**, in their
configuration or their profiles. The available types depend on the input module: this is neither a
global policy nor a brain decision. In the absence of a rule configured by the streamer, each module
declares a **default of its own input type**, configurable and replaceable: for instance, chat
triggered by a message containing the bot's name, voice by a wake word. Explicit rules replace that
default; any combination must be declared explicitly. The core evaluates the effective policy at
ingestion, before admission, run budgets and any model call.

| Target contract | Declaration and responsibility |
| --- | --- |
| `TriggerSpec` | Versioned declaration of the supported types and parameter schemas by the input module, in its manifest (section 4.1), for example `triggers: [probability, audience, keyword]`. The module provides the predicates, the facts specific to its source and its per-type default with its parameters. |
| Policy per input/channel | Rules selected and parameterised by the streamer among those capabilities, otherwise the default declared by the module; policy version identified at ingestion. Unsupported types, parameters and combinations are refused at validation. |
| Evaluation | Bounded function, deterministic on a normalised event, trusted context, injected configuration and dependencies, identical each time. RNG and clock are injectable, notably for the probabilistic rule; the draw and the elements needed to reproduce it are kept in a bounded, redacted form with the decision. |

The trigger turns **ingested event → work admissible for a brain run**. An acceptance does not yet
create a run and does not guarantee its admission. A trigger rejection is a decision that is handled
and traced, not an error; it launches no model. Deduplication keeps that decision inside its bounded
window: a redelivery of the same event does not draw again. Traceability follows the limits and
audit guarantees of section 2.3.

Order at ingestion: **normalisation → separate feeding of the channel context → effective trigger
(explicit rules or the type's default) → bounded admission → run**. Authenticity, validation and the
anti-loop exclusions (own outputs and internal events) are system invariants applied at the boundary
before the trigger; the security checks also stay active at execution. The streamer cannot disable
those invariants with a trigger rule. Load admission always stays active for accepted events: no
policy, not even "always", bypasses saturation. The trigger precedes the run budgets, without
removing the bounds of the transports, of ingestion or of its own evaluation.

| Input | Rule examples, illustrative syntax |
| --- | --- |
| Chat | `probability: 0.1`: 10 % chance per unique event; `audience: subscribers`: only subscribers' messages; `keyword: [hello, companion]`: configured keywords. |
| Voice | Types specific to the voice module, for example `wake_word` or `voice_command`: the module detects a wake word or a voice command and provides the normalised event the evaluation needs. Those capabilities are not imposed on every input. |

Combinations are possible according to the module's declared capabilities, for example subscribers
**AND** keyword **AND** 10 % probability. The operators and their evaluation order must be explicit
and testable; no implicit composition. Role/subscription predicates use a trusted context provided
by the platform or the provider, with provenance; the text of a message or of a transcription never
proves a role. Missing information does not satisfy a predicate that requires that role.

An observation returned to a run by `chat.read` or `audio.capture` does not trigger another run.
Voice triggers concern the spontaneous inputs emitted by the voice module when it supports that
capability; they do not promise continuous listening from the first audio capture. The per-type
default principle is settled; the exact syntax of declarations, parameters and compositions remains
an implementation contract to be specified in phase 0 for chat, then with each new input.

**Existing:** no configurable trigger engine is implemented; `BrainModule.handle_chat_message`
today calls the model for every valid message received, outside shutdown. That behaviour is not a
product default adopted for v2; phase 0 introduces per-input selection.

## 3. Agentic brain: run engine

### 3.1. Loop and task ownership

A run may end directly from `queued` on expiry (`timeout`, reason `stale_drop`) or on cancellation;
otherwise it goes from `queued` to `running`, then `finalizing` when an answer must be delivered,
and finally to a terminal state: `success`, `refused`, `error`, `timeout`, `cancelled` or
`external_unknown`. `success` requires the confirmation of every required output; an ending without
an output authorised by the product says explicitly `delivery = not_requested`. Intermediate calls
may fail and be recovered without failing the whole run. Any remaining uncertain effect is reported
in the summary; it forbids an overall summary of certain success.

At every step, the brain:

1. Builds a bounded context and the list of ready, authorised actions for that session.
2. Calls the model adapter, which returns either an `ActionProposal(name, arguments)` or a
   `FinalResponse`.
3. For an action, creates an `ActionCall`, waits for the executor, adds the structured observation
   to the transcript, then calls the model again if the budgets allow it.
4. For a final answer, selects the configured output and calls the same executor with the matching
   send tool. That delivery counts in the budgets; it is not a mandatory new model call.
5. Records the summary, updates admissible memory and releases the run's resources.

**Settled model adapter:** a single "OpenAI-compatible" adapter, limited to the classic endpoints in
the OpenAI-style Chat Completions format; endpoint, model and key stay configurable as today. The
targeted compatibility covers OpenAI backends, Ollama and local servers exposing that format,
subject to verified capabilities. No multiple proprietary adapters in v1. The adapter detects and
verifies the structured output the action proposals need, vision in phase 1, then audio when
required in phase 2. A backend lacking a required capability is refused explicitly, with no silent
removal of an image, an audio or an expected structure. Compatibility of the format alone does not
prove those capabilities.

One action at a time is executed within a run in phase 1. A backend response carrying several calls
is refused as an unsupported shape, with no partial effect. Explicit write actions are permitted
only by policy; they are tracked in the summary to avoid a second automatic delivery of the same
content. The initial vertical authorises no intermediate write: only the final answer may be sent,
with an explicit authorization rule applicable to that send.

The brain keeps all its tasks, including those detached from the input handler, until their
termination or explicit transfer to the shutdown supervisor. The executor likewise keeps in-flight
calls. A late result never reopens a finished run.

### 3.2. Action and call contract

Shared target contracts in `core/contracts.py`, registry/execution in `core/actions.py`; neither
file exists at the starting reference.

| Contract | Fields and rules |
| --- | --- |
| `ActionSpec` | Stable dotted `name`, `version`, `description`, `arguments_schema`, `result_schema`, nature `read` or `write`, required permissions, supported destinations/resources, timeout and idempotency policy. JSON Schema documents with a declared dialect and a verified contract version. |
| `ActionCall` | Version, `name`, action version, validated arguments, `conversation_id`, `run_id`, `call_id`, triggering event/message, destination, trusted principal, deadline, possible idempotency key. |
| `ActionProvider` | Async interface `invoke(call) -> ActionObservation`, identical for local and proxy; the registered handler belongs to a single module/provider. |
| `ActionExecutor` | Validates shape and bounds, resolves a single provider for the action/destination, checks availability and permissions with default deny, reads included, applies deadlines and resource conflicts, validates the result. |

A binding must designate **one executor** for an action and its destination perimeter. Two ambiguous
bindings fail preparation; no broadcast to several sinks and no automatic fallback after an
uncertain effect. Several providers may offer the same tool for disjoint destinations or with an
explicit selection by configuration.

The model proposes; **the executor refuses by default: without an applicable authorization rule,
every action is refused, a read included.** There is no self-authorised action. The check is
repeated on every call, even when the action appeared in the prompt.

**Authorization rules are set by the streamer (configuration/grants) and/or declared by the
input/output modules in their manifest/module, as declarative rules: each module knows its own
requirements.** Declaring an action, its `read` nature, its required permissions or its availability
does not by itself constitute a rule that authorises it. The format of the rules, their
combinations and their channel/session scope remain to be defined later (section 7); no implicit
priority order is fixed here. A list authorised at the start of the run is not a permanent
permission. The provider also applies its external constraints and, remotely, its own authorization.
Absent roles are not presumed; chat, OCR and observations are external data, unable to widen rights.

### 3.3. Observation and explicit terminal outcomes

`ActionObservation` contains the version, the correlation, `status`, some `parts`, the structured
data validated by `result_schema`, the provenance (`provider_id`, source, observation/capture date),
and possibly a normalised error (`code`, sanitised message, retry indication). Even an output action
returns an observation.

| Terminal `status` | Meaning |
| --- | --- |
| `success` | Read obtained or effect confirmed according to the provider's contract; external reference kept when available. |
| `refused` | No applicable authorization rule, or policy, destination, permission or availability forbidding the call; no effect engaged. |
| `error` | Invalid arguments, malformed response or certain failure; precise business code. A response lost after an effect was emitted does not belong here. |
| `timeout` | Deadline exceeded with no ambiguity about an external effect, for example an expired read. |
| `cancelled` | Confirmed cancellation with no uncertain effect. |
| `external_unknown` | The effect may have happened, but its confirmation is missing; includes timeout/disconnection/cancellation after an unreconciled emission. |

The `cause` field may distinguish timeout from cancellation behind `external_unknown`. The absence of
a provider or of a result is never a success. The executor normalises expected exceptions into an
observation; infrastructure failures also stay visible to the supervisor. A run cancellation keeps
its asyncio control: the terminal observation is recorded for the summary, without relaunching the
cancelled model.

Parts are typed: `text` with bounded text; `image_ref` with `attachment_id`, MIME, size, dimensions,
provenance and capture date. The audio phase extends that contract with `audio_ref`, MIME, duration
and possibly a text transcription. The result may combine several parts; no arbitrary Python
objects, no local paths assumed usable remotely, no base64 images in the events.

`AttachmentStore` (target: `core/attachments.py`) owns the bytes. It enforces a size per object, a
number of objects, a global and per-run volume, a TTL, and access limited to authorised recipients.
Active references are leased to the run until its deadline; when space runs out, the addition is
refused, with no unbounded growth and no silent eviction of an observation in use. End/cancellation
release the references; a residual expiry ensures cleanup. An expired reference returns an explicit
error.

The model adapter resolves the references and encodes the media in the backend's format only during
the call. A proxy transfers attachments into the authorised store or provides a bounded
authenticated resolution; a path on the PC is not an observation the server can use. The capture
provider applies the window/area selection and the permissions before acquisition and transmission;
without an applicable rule, no capture happens. Every observation, even a read of the local cache,
goes through the same default deny.

### 3.4. Admission, sessions and budgets

The target `BrainModule.handle_chat_message` handler validates and copies a bounded entry, while
ingestion separately feeds the per-platform/channel context, then has the effective per-input
trigger evaluated (section 2.4). Only an event accepted by that trigger moves on to immediate
admission; the handler does not wait for the model. The copy is independent of later bus mutations.
Network reading does not create an unbounded task per message: every intermediate source queue is
likewise bounded and has a saturation policy.

A scheduler owns a queue FIFO bounded per `SessionKey`, a global cap on pending work, a cap on
sessions and a limited number of workers. Each session `(platform, channel_id, viewer_id)` has at
most one active run; distinct sessions may progress in parallel, with fair distribution. Idle
queues/session locks are evicted. Actions sharing a channel resource, such as a poll or a scene, add
a **bounded** lock or queue at the provider: serialising the conversations is not enough.

**Settled decision:** every new message accepted during a run joins its session's bounded FIFO,
without merging or interrupting the active run. Each queued message has a finite wait deadline,
configurable per profile and at most equal to the run's total deadline computed at admission. If it
expires, the work is dropped before any model call: terminal state `timeout`, reason `stale_drop`,
`brain.run.completed` trace and counter with the wait duration; no start and no obsolete answer for
that message. The scheduler checks that bound while queued and before starting. Full queue or global
cap reached: the new work is rejected, traced by `brain.admission.rejected` with reason and counter,
with no model call and no replay promised. The chat context keeps the ingested message within its
own bounds, independently of admission, of a trigger rejection or of a stale drop. The generic
fallback decided below also applies to queue expiry, subject to the output budget and the total
deadline; it does not answer the stale content.

Acceptance by admission means only that the runtime owns work in memory. Source deduplication and
the context record a single ingestion decision before any long work; an explicit rejection by
trigger or for overload is a decision that is handled, not a successful execution. A technical error
before that decision may be retried. The short ingestion callbacks must be idempotent on the message
key, even when a later subscriber fails. A process crash may lose an accepted run: no durable
guarantee before phase 3+.

Each profile configures finite, validated values for: maximum wait, total deadline **from admission
to delivery**, model turns, action calls (delivery included), input/output/cumulative tokens,
observation and media volume, per-model/action delay, rate and budget per channel. The defaults
settled below can all be replaced per profile; the other limits must also be defined and validated,
with no numeric value invented here. No agentic profile is usable with omitted or infinite limits.
The runtime reserves margin for the output and bounds the next call by the remaining time. If the
backend does not provide real tokens, a conservative estimate is reported as such and the
input/output sizes stay capped.

| Budget | Settled default value |
| --- | --- |
| Model turns per run | 5 maximum |
| Delay per model call | 30 s |
| Delay per action, delivery included | 10 s |
| Total deadline admission → delivery, queue wait included | 60 s |
| Cumulative input + output tokens per run | ≈ 8K |
| Maximum size of one observation or media | ≈ 5 MB |

The approximate values become explicit numeric caps in the implementation profiles. The capability
checks of the single adapter (section 3.1) precede the use of the backend; the model calls and the
media encodings stay subject to the budgets. Accepting a trigger or a run grants no action right:
reads, captures and delivery each require an applicable rule. Exceeding a bound stops the work
concerned with an explicit summary, with no obsolete business answer and no deadline renewal.

**Settled decision — generic fallback:** on budget overrun or expiry, send to the chat a message
such as "I could not answer in time". That fallback is enabled by default and disableable by
configuration; it requires no new model call. It goes through the executor, requires an applicable
authorization rule and **itself consumes the output budget**, including the action call and the
channel quotas. The margin reserved for the output must allow that send within the remaining bounds.
If the fallback is disabled, not authorised, or if the output budget or the total deadline no
longer allow its delivery, no message is sent and the reason is traced. No send after the total
deadline; the fallback does not turn the failure of the initial work into a success.

The local deadline uses a monotonic clock. A proxy transmits a UTC deadline and a remaining time;
the receiver applies a conservative limit and never renews the budget at every hop. The protocol
must test clock skew and latency; wall-clock timestamps alone are not enough.

The anti-loop is a system invariant that the configurable triggers cannot disable; it combines the
existing anti-echo filter, the exclusion of traces as triggers, per-channel quotas, the turn limit
and the detection of action/argument/observation repetitions without progress. Repeated reads may be
useful when their timestamp changes, but they stay budgeted. After an uncertain effect, no blind
retry: use an idempotency that is actually supported or reconcile the state. `call_id` allows
correlation, without by itself guaranteeing exactly-once execution.

### 3.5. Three separate memories, all bounded

| State | Owner and target policy |
| --- | --- |
| Run transcript | Brain; calls, observations and provisional output, bounded in tokens/bytes and deleted at the end unless a summary is authorised. No storage of private reasoning. |
| Conversation history | Brain; evolution of `_histories`, indexed by `(platform, channel_id, viewer_id)`, bounded in sessions, complete exchanges, tokens/bytes and TTL. It asserts as delivered only the acknowledged outputs; uncertainties stay explicitly marked. |
| Chat context | Read module; fed quickly at ingestion, independently of triggers, admissions and runs, even without an answer. Window per platform/channel, limits in messages/bytes/age, dated and paginated reads. |

**Settled decision — memory extensible by modules:** persistence, long-term memory and other memory
strategies plug in like the other modules, with the common declaration, configuration, activation
and lifecycle (section 4). They use the existing contracts where suitable, or a dedicated, versioned
contract extension. The minimal interface provides bounded reads and writes (count, volume and
delay), a session perimeter provided by the runtime (`SessionKey`), and an explicit TTL with expiry
and erasure. It preserves the separation of the three memories and the isolation of the sessions;
the channel context keeps its distinct perimeter. Accesses stay controlled, with no implicit right
and no session choice by the model. The interface details are to be specified when the module is
implemented, without imposing a storage engine on the core.

**Without a memory module:** the core's states are volatile, with short TTLs and finite bounds; the
media are cleaned up at the end of the run per section 3.3. File audit is optional, with rotation
and exclusion of sensitive data as per section 2.3; it does not serve as recovery memory. No durable
persistence is implicit. Durations and limits belong to the profiles; any durable memory module must
declare its retention, access and erasure policies. Its delivery depends on a need observed in
phase 3+.

`EventBus.list_events()` remains a bounded inspection tool, not conversational memory: the current
history is recorded in completion order and does not necessarily include the entry in progress.
Context snapshots are ordered at ingestion; the triggering message is available before its run
starts.

## 4. Modules: declaration, execution and topology

### 4.1. Versioned manifest and activation

The target extends `modules/*/module.yaml` with `manifest_version: 2`, `runtime_api: 2`,
`settings_schema`, `actions`, `triggers` for the input modules and lifecycle
dependencies/capabilities. The `triggers` declarations describe the supported types, versions,
parameter schemas and combinations, plus the default specific to the input type (`TriggerSpec`,
section 2.4); the explicit rules of each input/channel config/profile replace that default and stay
distinct from the actions exposed to the model. The input/output modules may also declare their
authorization rules in the manifest/module, separately from the triggers and the `ActionSpec`; the
format and the composition with the streamer's grants are to be specified at implementation. No
implicit grant is inferred from a capability declaration. Each `actions` entry carries the
`ActionSpec` fields; a schema may be inline or referenced in the module's directory, with path
validation. `produces`, `consumes`, `middleware`, `order` keep their event meaning. Declaring
`produces: [channel.chat.send]` does not authorise the `chat.write` action.

The loader distinguishes three views:

1. **Discovered**: valid manifest, even disabled; immutable catalogue for introspection, never
   presented as a list of usable tools.
2. **Registered and ready**: async handler registered during activation/preparation, compatible
   schemas, usable resources and authentication. Registration alone does not mean ready.
3. **Authorised**: ready subset filtered by principal, session, destination and applicable
   authorization rule; without a rule, the action is excluded, a read included. Only that view is
   exposed to the model; the check is repeated at execution.

A versioned `RuntimeContext` (target: `core/runtime.py`) injects the bus, registry/executor, store,
trigger engine, injectable RNG and clock, supervision, diagnostics and transport factories. The YAML
configuration contains only data; test callables progressively leave the settings. The v2 API
targets `validate_settings(settings)` then `activate(context, settings, catalog)`, which returns a
prepared handle and registers its async handlers through
`context.actions.register(spec, handler, provider_id)`. Activation does not start the producers.

The core validates YAML, environment, paths and versions; each module validates its business
constraints, for **every enabled module before its network effects**. Imports must stay free of
external effects. Disabled manifests stay validated; their secrets are neither resolved nor their
business settings required. `${NAME}` stays a whole reference; number conversion and bounds belong
to the module's schema/hook. A local model requires a key only when the configured
OpenAI-compatible backend requires one.

### 4.2. Lifecycle by phases

The v2 handle exposes idempotent, bounded async hooks; a module with no role in a phase provides an
empty operation. `core/main.py` coordinates those phases without sorting by business names.

| Phase | Guarantee |
| --- | --- |
| Validation, then preparation by `activate` | All settings are validated; consumers, handlers, resources and supervised tasks are registered without emitting any input. Clean up locally when the handle has not been returned yet. |
| Preparation barrier then `start_inputs()` | All required consumers and providers are ready before the first input; the trigger policies (default included) and the declared authorization rules are validated, the default deny is active and the trigger evaluation as well as admission are operational before the producers open. Global readiness after a successful start. |
| `stop_inputs()` | On shutdown, cut the new sources and finish the short in-flight admissions; then close admission. Keep the transports the outputs and reads of the running runs need. |
| `drain(deadline)` | Finish the accepted work within the shutdown budget or cancel it explicitly; keep the executors and outputs available until the terminal summaries. |
| `close()` of the ordinary resources | Close transports and stores after the calls; collect the errors without ignoring the other modules. |
| `flush(deadline)` then closing of the observation services | Finish the traces after the last closures, then close the audit. That role is declared, not tied to the name `audit`. |

One global deadline covers startup and another shutdown; local delays do not add up without a cap.
The supervisor observes task errors, degrades providers and withdraws their ready capabilities. A
failure of a mandatory provider makes the global health degraded or stops it, according to an
explicit policy.

Cancelling a coroutine does not prove that a native capture or a thread writer has stopped. The
module must choose a bounded or isolatable execution and report tasks that resist; the CLI keeps a
final process watchdog with a non-zero exit and reported possible losses. Embedded use exposes the
close failure to the process owner; it cannot guarantee killing an arbitrary thread. Partial startup
and cancellation follow the same cleanup responsibilities.

### 4.3. Local provider or remote proxy

The target configuration adds explicit bindings: tool name/version, `provider_id`, permitted
destinations, `transport = local|remote`, reference of the local module or authenticated remote
endpoint, constraints and budgets. That vocabulary is a **target schema**, not keys accepted by
`config.yaml.example` today. The brain always calls `ActionExecutor`, never a URL or a capture API.

| Target topology | Distribution |
| --- | --- |
| PC profile — phase 1 | Brain, platforms/sinks and store on the streamer's PC; a PC agent on that same machine to expose the local capabilities through the proxy. Local provider bindings stay supported; the platform/model services may be external. |
| Server + PC agent profile — phase 1 | Brain, platforms/sinks and store on a remote server; a small agent on the streamer's PC exposing capture and other local capabilities through the proxy. Audio and scene control arrive in phase 2. |
| Standalone server — no PC capability | Brain and network modules on a server; a capture needs a source reachable from that server. This case does not replace the server + PC agent profile retained for phase 1. |

**Settled decision — phase 1:** two distinct configuration profiles place the brain and the
platforms/sinks on the streamer's PC or on a remote server. A small PC agent exposes the local
capabilities to the brain through a proxy **WebSocket JSON with a pairing token**, limited to **one
PC agent per brain in v1**, with reconnection and backoff. Attachments are transferred within the
store's bounds (section 3.3); observations use references, never local paths assumed usable
remotely.

The versioned proxy protocol carries `ActionCall`/`ActionObservation`, cancellation, health control
and attachment resolution. It imposes peer authentication, network encryption, local control of
destinations/resources with default deny, a read included; peer authentication is not an action
grant. It also imposes payload and work limits, deadlines and bounded deduplication. A retransmitted
call must not launch a second execution while its idempotency entry is kept; after expiry or a crash
it may remain uncertain. A reconnection does not authorise the automatic replay of writes.

Spontaneous remote inputs use a source adapter that authenticates and normalises the events before
publishing them on the local bus. That does not distribute `EventBus` and does not use its
subscriptions to correlate action returns. The chosen transport is to be implemented and tested in
phase 1 with the requirements above; the exact message format, the profile syntax and the backoff
parameters are to be specified at implementation. The common interface and the support of the remote
mode are immediate requirements, not already delivered capabilities.

### 4.4. The seven starter modules

The seven functions below constitute the target kit, delivered progressively in phases 1–2. They are
configurable units; they may share a platform provider and its connections. The proposed directory
names do not impose seven credential duplications or seven Twitch connections. **Today only
`twitch`, `brain`, `audit` exist.** `modules/twitch/` stays the shared Twitch adapter; the new tools
below are not already registered.

| Target starter module | Tools, meaning and result | External dependencies and local/remote |
| --- | --- | --- |
| Chat read — `modules/chat_context/` | `chat.read`: input/observation, destination + window/limit → normalised messages, cursor, date and coverage. | Cache fed by the sources; Twitch through EventSub in `modules/twitch/`. No platform request needed to read the cache. Provider near ingestion or proxy; the result indicates a partial/expired window. |
| Users read — `modules/users/` | `users.read`: input/observation, destination + bounded pagination/filter → identities, available trusted roles, date and coverage. | Twitch provider through suitable Helix operations and available EventSub enrichments. Authorizations and availability verified at implementation; it does not promise an exhaustive list of viewers. Local PC or server/proxy behind the same contract. |
| Chat write — `modules/chat_output/` | `chat.write`: output/action, `Destination`, text, optional parent → confirmation, external identifier and destination. | Delegates to the Helix send of `modules/twitch/` for Twitch; other adapters for other platforms. PC, server or proxy; quotas and rights stay with the provider. |
| Stream/game screenshots — `modules/capture/` | `screen.capture`: input/observation, authorised source/area → `image_ref`, MIME, dimensions, provenance and timestamp. | OBS, OS capture, window or game API depending on the provider. Acquisition on the machine with access to the source; proxy when the brain is remote. Source, area and cadence configured in the module; point observation requested by the brain, with no background capture. |
| Audio output — `modules/audio_output/` | `audio.speak`: output/action, text + authorised voice/output → confirmed playback state; `audio.play`: audio reference → playback state. | Local TTS or remote service, audio device/OBS. The TTS backend may be a server; the final playback is on the output machine, possibly through the proxy. Success means playback finished per contract, not merely a synthesis created. |
| Audio capture — `modules/audio_input/` | `audio.capture`: input/observation, source + bounded duration → `audio_ref` and optional dated transcription. | Microphone, system mix or OBS; local or remote transcription when configured. Acquisition near the device, bounded transport of the segments when the brain is a server. Capture on the brain's request; sources/outputs and duration belong to the modules' configuration. The spontaneous voice inputs of section 2.4 are a distinct capability, with no implicit background capture. |
| Stream interaction — `modules/stream_control/` | `stream.scene.set`: output/action, authorised scene → confirmed OBS state; `stream.poll.create`: output/action, bounded question/options/duration → poll identifier and state. | Scenes through an OBS provider on the PC or through the proxy; Twitch polls through Helix, with EventSub state tracking according to the available capabilities. Distinct permissions and mutual exclusion per resource. Later moderation requires its own tools/permissions, with no implicit authorization. |

Capture source (OBS/OS/game), area, cadence and audio sources/outputs belong to each module and its
configuration: neither a global product decision nor a phase gate for the core. The natural cadence
is **action-driven**: the brain requests an observation when it needs one, the module performs the
capture, with no background capture. Every tool in the table, reads included, requires an applicable
authorization rule and respects the budgets.

The details of endpoints, scopes, vision/audio formats and supported platforms must be verified at
each implementation; this table fixes the responsibilities and the expected outcomes. A module
without its external dependency stays discovered but not ready. The available features must be
visible in the runtime state, not only in the prompt.

## 5. Migration from the existing code

### 5.1. Review blockers and target answer

| Blocker | Verified existing | Target answer and location |
| --- | --- | --- |
| B1 — Admission | `_consume` awaits the publication; Twitch's `_publish_lock` covers the model and the output. | Admission and FIFO bounded per session with a deadline and `stale_drop`, brain workers; `modules/twitch/__init__.py` and `modules/brain/__init__.py`, phase 0. |
| B2 — Lifecycle | `_close_activations` sorts Twitch first, audit last; Twitch owns the publications in progress. | Generic phases and run ownership; `core/main.py`, `core/loader.py`, the three modules' handles, in the same batch as B1. |
| B3 — Actions ≠ events | `[send:…]` tags, `delivery_status` mutation, no single executor and no return to the model. | `ActionExecutor`/registry and terminal acknowledgement from phase 0; loop and observations in phase 1. `channel.chat.send` becomes a compatibility route. |
| B4 — Capabilities | Catalogue of every manifest, disabled ones included, used in the prompt. | Separate discovered, ready and authorised; manifests + registry/runtime; filtered exposure and default deny at execution, reads included. |
| B5 — Sessions | `_histories` indexed by viewer; copy lock, no serialisation of one exchange. | Sessions platform/channel/viewer, global context separated per platform/channel fed at ingestion, per-session queues and global workers in phase 0; distinct transcript in phase 1. |
| B6 — Retention | Bus history, audit queue and Twitch dedup unbounded; viewer memory bounded in number only. | Limits in count/volume/age, saturation policy, dedup with an explicit window and a bounded media store. |
| B7 — Validation | `core/main.py`'s `_MODULE_REQUIRED_FIELDS` duplicates `_Settings.from_mapping`; references of disabled modules resolved. | Module hooks and schemas before network effects, separate runtime context, secrets of the enabled modules only. |
| B8 — Observability | Audit writes `type`/`payload`, omits `metadata` and can silently lose records. | Correlated traces, bounded health and loss counters; core in phase 0, complete loop instrumentation in phase 1. |

### 5.2. What is kept, what explicitly evolves

**Kept:** `EventBus.subscribe/publish`, `*`/`**` patterns, stable order and publication errors;
directory/manifest discovery by `ModuleLoader`; injectable HTTP/WebSocket/writer transports; real
modules behind fake transports in integration; the Twitch anti-echo filter, reconnection and
redacted diagnostics. The nine files `tests/test_{bus,loader,main,twitch,brain,audit,examples,integration,shutdown}.py`
constitute the non-regression base, not a proof of v2.

**API transition:** absence of a manifest version means v1. An explicit path keeps
`activate(bus, settings, catalog)` and `close()` for v1 modules. It does not claim to extract their
phases magically: a v1 producer incompatible with the barrier must be migrated or refused in the
agentic profile. The three built-in modules migrate together; no hidden context attribute is added
to the bus.

**Output transition:** the tags stay confined to the historical model adapter. The
`channel.chat.send` route and the `chat.write` tool converge on a common send service, with a single
path chosen per invocation. The v2 brain uses the executor directly and does not republish the
command in parallel. Historical clients can still read `delivery_status = sent|failed`, but the new
actions have the complete result, `external_unknown` included. The absence of a status is no longer
interpreted as a v2 delivery. The reply parent is transmitted explicitly from `Destination`.

**Authorization and model transition:** no implicit right of the existing code is carried into v2.
The phase 1 tests and profiles will have to configure explicitly the grants they need, including
`chat.read`, `users.read`, `screen.capture` and `chat.write` when used; output compatibility does not
bypass the executor. Endpoint, model and key stay configurable behind the single OpenAI-compatible
adapter; structured output and media capabilities must be verified, without multiplying proprietary
adapters.

**Decided target retention:** bus history circular, limited in events/bytes/TTL; audit queue limited
in records/bytes, non-blocking admission and dropping the new record on overload, with a loss
counter and an aggregated diagnostic outside the saturated queue; source dedup limited by size and
TTL. The v2 dedup guarantee covers **the retained window**, not the whole activation. A repetition
after eviction may be processed again. A longer guarantee requires suitable storage and is not
promised by the in-memory core.

The policy without a memory module is settled: everything volatile, short bounded TTLs, media
cleaned at the end of the run, optional file audit with rotation and exclusion of sensitive data
(section 3.5). Durable memory requires an explicitly configured optional memory module; its access,
retention and erasure policies are declared by the module, with no implicit persistence in the core.

These choices change historical guarantees: R1 for history inspection, R4/AC10 for deduplication and
R6/AC17 for audit exhaustiveness under overload. R2/R3 evolve with the activation/configuration
contracts; R5/R7 with the model and the modules. **Phase 0 will have to version the requirements and
adapt the tests concerned in its own changes. This documentary commit modifies neither spec, nor
plan, nor tests.** Compatibility means an explicit, tested transition, not the silent preservation
of promises incompatible with finite bounds.

Packaging is part of the delivery: `pyproject.toml` currently includes only `core*`. A v2
installation will have to provide the starter modules, their manifests and installable
configuration profiles; running from the checkout is not a proof of a working distribution.

## 6. Phased roadmap and exit criteria

The v2 phases do not renumber the historical steps P1–P9. The criteria below are to be written and
executed during implementation; the 189 current tests do not cover them all. Section 7 summarises
the settled product decisions; no blocking product question remains open there. The technical
details flagged there are to be specified at implementation.

### Phase 0 — Coherent core before multi-turn

**Indissociable batch: per-input trigger engine + bounded admission + lifecycle by phases +
action registry/executor + session identities and scheduling + bounded retention.** Add module
validation, supervision and minimal traces. Deliver `TriggerSpec`, the capability declarations and
their per-type defaults, per-input/channel configuration and default action denial, then evaluation
before admission; apply the first policies to chat to replace the current systematic model call
before the agentic vertical. The voice types arrive with the audio module's capabilities in phase 2.
Initially keep one model call per unit of work; route its output through the explicit result of the
send service. Define the media references and test the store's quota before integrating a capture.

Exit criteria:

- Two inputs/channels can apply distinct policies; without an explicit rule, the type's default
  applies (chat: the bot's name), and a configured rule replaces it; undeclared types/combinations
  refused before the network. Probability, trusted subscription and keyword are verified with
  injected RNG/clock: the same decision is reproducible, no new draw on a duplicate within the dedup
  window. A trigger rejection is traced with no admission and no model call; an acceptance stays
  subject to saturation and to the anti-loop/security invariants.
- A fake model held waiting does not prevent the reception of a second message or of a keepalive;
  the queues and tasks stay within their limits under overload, with counted rejections. An expired
  wait deadline produces a traced `stale_drop` with no model call and no obsolete answer, verified
  with an injected clock.
- Two messages of one session see the histories in order; another session progresses within the
  limit of the workers; the same viewer IDs on two platforms/channels do not share their memory. Two
  viewers of one channel have distinct conversations and read, when authorised, the same global
  context, fed even after a trigger/admission rejection or a `stale_drop`.
- A deliberately slow consumer activation lets no input through before the barrier. A fourth
  fictional producer/sink module starts and stops without adding a name in `core/main.py`.
- After the admission handler returns, a shutdown keeps the output transport until the detached
  run's summary; exhausted delay: explicit cancellation and status. Cover every phase, partial
  startup, `CancelledError`, a resisting task and a blocked writer.
- A duplicate binding, an incompatible schema, a disabled module, a not-ready provider or an absent
  sink produce no false success. Without an applicable rule, read as well as write return `refused`
  without invoking the provider; the authorised scenarios configure their grants. The business
  validations fail before the network, with no secret in the diagnostics.
- Exceeding the bus/audit/dedup/memory/store limits: bounds respected and eviction/rejection
  behaviour proved with an injected clock, including a replay after the dedup window expires.
- `tests/test_shutdown.py` and the integration assertions are adapted to the new task ownership: no
  longer require "output before input" as a run invariant. The old contracts that are preserved stay
  tested; the R1–R7 changes are documented/versioned.

### Phase 1 — Agentic text + image vertical, local and remote

Deliver the loop and the single OpenAI-compatible adapter with structured output, the budgets at the
defaults settled in section 3.4, the multimodal observations and the operational store. Provide chat
read, bounded users read, chat write and a simple capture. Deliver the small PC agent and its
WebSocket JSON proxy with a pairing token, one agent per brain in v1 and reconnection with backoff,
without distributing the bus. Provide two distinct profiles: brain and platforms/sinks on the
streamer's PC or on a remote server, with local capabilities exposed by the PC agent and attachments
transferred by reference within the store's bounds (section 4.3). Provide the corresponding
installable modules/manifests.

Exit criteria:

- Grants explicitly configured in the tests/profiles for every action of the scenario, reads and
  capture included. Scripted fake model: `chat.read` → text observation → `screen.capture` → image
  observation → final answer → acknowledged `chat.write`. Exactly one send, no intermediate send;
  continuous IDs and the image reference cleaned after the run.
- The same scenario through a local provider then through the proxy on a simulated transport
  boundary: same contract, no capture branch in the brain. A real test on two processes over
  WebSocket JSON for serialisation, valid/invalid pairing token, the one-agent-per-brain limit,
  reconnection with backoff, cancellation and bounded transfer of attachments by reference with no
  remote local paths; a PC/server trial recorded before declaring that topology delivered.
- A configured Chat Completions backend verified for structured output/tools and vision through the
  single adapter; a backend without a required capability refused before the scenario, with no
  silent loss of images. The fake model proves the orchestration, not the quality of the real model.
- Absence of an applicable rule (even for `chat.read`), forbidden/disabled actions, invalid
  arguments, availability changing during a run, an image too large/expired, a timeout then a
  late/double answer, cancellation at each step, a proxy break after an effect was emitted: correct
  summary, no blind retry and no leak of tasks/attachments.
- Turns, repeated actions, tokens, wait, delivery deadline and saturation stop the work concerned
  according to the settled bounds and the fallback policy decided in section 3.4: generic message
  enabled by default, disableable, subject to the authorizations and consuming the output budget;
  verify the silence when disabled or when the bounds forbid the send; an uncertain external result
  is never memorised as a confirmed send.
- `users.read` states pagination, freshness and coverage; no partial list presented as the complete
  audience. A second fictional platform validates the absence of a Twitch dependency in the
  contracts/brain.
- Installation in an empty environment then effective discovery of the modules/manifests; smoke test
  of the PC and server/proxy profiles with no embedded secret.

### Phase 2 — Audio and stream interaction

Complete the seven starters: audio capture/output, scenes and polls; enrich the platform adapters
without introducing a business command in the core. Verify the required audio capabilities of the
single model adapter and configure the rules/grants explicitly before the effects; the choice of
audio sources/outputs stays in the modules.

Exit criteria:

- Capture of a bounded segment → audio/transcription observation → audio answer; duration/byte
  limits, playback end, interruption and device unavailability verified locally and through the
  proxy.
- Authorised scene and valid poll: one confirmed and traced effect; missing permission or target
  outside the perimeter: zero effect. Two concurrent sessions on a shared resource respect the
  provider's serialisation.
- Loss of confirmation after a command: reconciliation or `external_unknown`, with no second poll
  creation and no false confirmation. Shutdown during capture/playback/scene control respects the
  global budget.
- The seven units can be enabled/disabled with an honest view of the capabilities; the optional
  audio/OBS dependencies do not prevent a chat-only profile. Packaging and integration trials
  specific to each provider are documented.

### Phase 3+ — Only according to observed usage

| Conditional extension | Trigger and criterion before delivery |
| --- | --- |
| Optional memory modules through the modular architecture (persistence, recovery, long-term memory) | Observed need for retention or for surviving a restart; existing contracts or a dedicated extension per section 3.5. Verify bounded reads/writes, per-session isolation, TTL, access and erasure; when it is recovery, test a crash before/after an effect, idempotency and recovery without a false send. No persistence imposed on the core. |
| Multi-instance brain, multiple provider instances, possible distributed bus | Measured load or availability; prove exclusive ownership of the sessions, fencing, partition handling and the absence of double effects. The phase 1 PC proxy does not wait for this phase. |
| Hot reload | Need for reconfiguration without stopping; unsubscription by owner, draining, atomic removal of the tools and no orphaned references/tasks. |
| Module sandbox | Execution of untrusted code; process boundary and tested capabilities. Today as in phase 1, a Python module inside the process remains trusted code. |
| Tool parallelism, workflows, summaries/search | Demonstrated gain; conflict tests, aggregated budgets, causality and quality. No framework, broker or dynamic graph imposed on the core. |

## 7. Settled product decisions and implementation details

**Settled decisions (batch 1):** trigger default declared per input type and replaceable (2.4);
run/memory sessions per platform + channel + viewer, chat context separated per platform + channel
(2.2, 3.4–3.5); FIFO bounded per session with a deadline, `stale_drop` and rejection of new work when
full (3.4); default budget values accepted and configurable (3.4); no self-authorised action, default
deny and rules from the streamer and/or the modules (3.2, 4.1); single OpenAI-compatible Chat
Completions model adapter, verified capabilities and incompatible backend refused (3.1). Those
decisions are no longer to be arbitrated.

**Settled decisions (batch 2, final):** the last three arbitrations are resolved below. **No blocking
product question remains open.** The technical sub-details are to be specified at implementation,
within the framework of the settled decisions.

| Topic | Status and reference section |
| --- | --- |
| Behaviour on budget overrun/expiry (4b) | **Decided — section 3.4:** generic fallback message to the chat, enabled by default and disableable by configuration. It consumes the output budget, requires an authorization and does not extend the deadline; no send when the bounds forbid it. |
| Detail of the permission system | **Principles decided — sections 3.2 and 4.1:** default deny, reads included, rules from the streamer and/or the modules with no implicit grant. Exact format, composition, channel/session scope and configuration syntax: **to be specified at implementation**. Explicit grants in the phase 1–2 tests/profiles (section 6). Moderation is outside the perimeter of the retained tools; its future tools will have to declare their own permissions (section 4.4). |
| Retention after a stream/restart and data access (8) | **Decided — sections 3.5, 5.2 and phase 3+:** domain extensible by optional memory modules, through existing contracts or a dedicated extension. Without a module: everything volatile, short bounded TTLs, media cleaned at the end of the run and optional rotating file audit excluding sensitive data. No implicit durable persistence. Durations and limits belong to the profiles; durable retention, access and erasure belong to the contract and the configuration of the chosen module. |
| Deployment profiles and first proxy transport (9) | **Decided — sections 4.3 and 6, phase 1:** brain and platforms/sinks on the streamer's PC or a remote server, distinct profiles, a small PC agent exposing the local capabilities through a WebSocket JSON proxy with a pairing token. One PC agent per brain in v1, reconnection with backoff, attachments transferred within the store's bounds and referenced with no remote local paths. Exact formats and parameters: **to be specified at implementation**. |

**Module configuration, outside the core's arbitrations:** capture source (OBS/OS/game), area,
cadence and audio sources/outputs belong to each module and its configuration, with the mechanics in
the module, never in the brain. Those choices are neither a global product decision nor a phase gate
for the core. Capture is naturally action-driven, on the brain's observation request, with no
background capture; every capture stays subject to the explicit authorization rules and the budgets.
The phase scenarios use a configured source to verify the contract, without imposing that source on
the product.

## 8. Addendum — input ownership (phase 5)

Added by phase 5 (spec `phase5-automation-bridge`, plan decisions D1–D8). Normative for the kernel:
it states the rule by which at most one module ingests each input stream. The rule names no vendor
and no platform; the optional automation bridge that motivated it is documented in
[`docs/automation-bridge.md`](automation-bridge.md).

### 8.1. Vocabulary

- **Input families** (`INPUT_FAMILIES`, closed): `chat`, `viewers`, `cues`.
  - `chat` — ingestion of `channel.chat.message` events of the kinds `CHAT_FAMILY_KINDS`, that is
    `message` plus the seven `PLATFORM_NOTICE_KINDS`, per platform (D1). It does **not** cover
    `watch_tick` (never published on the bus, evaluated by the `watch` module through the shared
    engine) nor `cue`.
  - `viewers` — the viewer read `users.read`, per platform. It is the one family gated by an action
    (`INPUT_FAMILY_GATED_ACTIONS`).
  - `cues` — events of the kind `cue`, per platform: what an operator automation deliberately routed
    to the companion. `cue` is the tenth member of `EVENT_KINDS`, outside `PLATFORM_NOTICE_KINDS`.
- **Declaration.** A v2 manifest may declare the optional key `input_families`: a list of
  `{family, platforms, default}` entries, `platforms` being concrete names or the single wildcard
  `*`. A malformed declaration is refused at discovery (`parse_input_families`). A v1 manifest may
  not declare it.
- **Profile.** The optional top-level `inputs` block maps family → platform (or `*`) → owner: one
  enabled module name or the literal `none` (`INPUT_OWNER_NONE`, deliberately unowned).
- **Mapping an event to a pair.** `input_family_of_event(event)` returns `(family, platform)` for a
  `channel.chat.message` of a `chat` kind or of kind `cue`, and nothing for any other event.

### 8.2. Resolution (D2)

Ownership is resolved at preparation, before settings validation and before any module is
prepared, from the enabled manifests and the `inputs` block (`resolve_input_ownership`, pure). The
domain of a family is its wildcard row, every pair an enabled module declares and every pair the
`inputs` block names; a platform outside it is answered by the wildcard row. For one pair:

1. the explicit `inputs.F.P` entry;
2. otherwise the explicit `inputs.F.'*'` entry;
3. otherwise the single enabled module declaring `(F, P)` concretely with `default: true`;
4. otherwise the single enabled module declaring `(F, '*')` with `default: true`;
5. otherwise the pair is unowned.

A concrete default declarer beats a wildcard one. Refused, every refusal collected and reported as
one value-free line each, by `run` and `--check-config` alike and before any transport opens: two
default declarers at the same specificity (naming family, platform and both modules), an owner
list of two or more modules, an owner that is not enabled or does not declare the pair (an owner
under `*` must declare `(F, '*')`), a family outside the vocabulary. The result, `InputOwnership`,
gives each row its owner, its origin (explicit, default rule or unowned) and its candidates; the
configuration UI renders it read-only.

### 8.3. Runtime guard (D3, D4)

Each module reads the pairs it owns through `ModuleContext.inputs`, an `OwnedInputs` view. When the
loader installed an ownership table, `ModuleContext.bus`, `triggers` and `chat` return
module-scoped facades sharing one predicate:

- a module that **declares** `input_families` passes a pair only when it is that pair's owner;
- an **undeclared** module — every v1 module, and a v2 module without the key — passes a pair
  unless the pair has a resolved owner or was set to `none` explicitly, so an unconstrained
  configuration behaves exactly as before the rule existed.

A refused bus publication reaches no subscriber, not even a middleware ordered first, and
increments the counter `input_ownership_rejections` once (D4). A refused `triggers.evaluate`
returns a refused decision with the reason `input_not_owned`, and a refused `chat.append` is
dropped, both without counting. The three facades of one module share one value-free diagnostic,
the trace `input.ownership.rejected` (module, family, platform), emitted once per pair and never
per event. Without a table, the shared objects are returned exactly as before.

**The guarded v1 route.** The v1 entry point `activate(bus, settings, catalog)` receives an
`_OwnedBus` facade for the module whenever the resolved table constrains any pair — at least one
pair has a resolved owner or is set to `none` (`InputOwnership.constrains_any()`). The facade
applies the undeclared-publisher predicate to `publish` and delegates every other attribute to the
shared bus. When the table constrains nothing, the v1 route keeps the bare bus: every pair is then
unowned, so the predicate would pass every event — identical behaviour, not an exemption.

### 8.4. Gated binding (D5)

A family gated by an action is bound only for the platforms its module owns. `ActionBinding` gains
the additive field `excluded_platforms` and `ActionRegistry.bind` the matching keyword: the owner
of the wildcard row binds the wildcard destination minus the platforms assigned to another owner,
and an owner of a concrete row binds that platform. Overlap and containment ignore excluded
platforms, so no two bindings of `users.read` overlap and `AmbiguousBindingError` stays unreachable
for a valid ownership. Only the runtime's `ModuleActions` facade passes an exclusion.

### 8.5. Engine identities (D6)

`TriggerEngine.add_companion_identities(identities)` adds identities to those the engine's
anti-echo invariant refuses: additive, idempotent, case-folded like the constructor's, it never
removes one and validates every identity before adding any. An input that relays another
transport's chat — it learns the companion's account from its own settings, after the engine was
built — calls it at activation, before any evaluation.

### 8.6. Ownership-aware settings hook (D7)

Because ownership is resolved before settings validation, the loader calls a module's settings
hook as `validator(settings, owned_inputs=view)` when, and only when, the hook's signature declares
the keyword parameter `owned_inputs`; every other hook is called exactly as before. A module can so
refuse at `--check-config` a configuration that is only invalid for the pairs it owns, such as an
owned chat pair without a companion identity for it.
