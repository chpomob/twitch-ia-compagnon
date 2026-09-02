---
name: "twitch-ia-companion-mvp"
version: "1.0"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec]
targets:
  - file: core/bus.py
    description: "Add the event publication, wildcard subscription, ordered handler-chain, and event-history contracts."
  - file: core/loader.py
    description: "Add discovery, manifest validation, capability collection, and module activation."
  - file: core/main.py
    description: "Add configuration loading, core startup, module lifecycle coordination, and failure reporting."
  - file: config.yaml.example
    description: "Document a runnable configuration for the core, Twitch, brain, and audit modules without real secrets."
  - file: modules/twitch/module.yaml
    description: "Declare the Twitch module's chat input and output event capabilities."
  - file: modules/twitch/__init__.py
    description: "Add Twitch EventSub ingestion and Helix chat-message delivery behavior."
  - file: modules/brain/module.yaml
    description: "Declare the brain module's chat input and routed output capabilities."
  - file: modules/brain/__init__.py
    description: "Add context construction, OpenAI-compatible LLM invocation, and tagged-output routing behavior."
  - file: modules/audit/module.yaml
    description: "Declare the passive catch-all audit middleware and its order."
  - file: modules/audit/__init__.py
    description: "Add non-blocking structured stdout logging for all events."
---

# Twitch IA Companion MVP

## Problem

Twitch streamers need a small, open-source foundation for adding an AI participant to a livestream without coupling event transport, platform integration, language-model behavior, or observability. The MVP must prove that independently declared modules can be discovered from manifests, exchange events through a deterministic bus, and be configured from one file. It must also demonstrate a complete chat path: receive a Twitch chat message, ask an OpenAI-compatible model for a response, route the response, send it back to Twitch, and audit the traffic.

The repository currently contains no implementation sources. This specification therefore defines the initial external behavior and module contracts. It assumes Python is the runtime, event type names are dot-delimited strings, configuration and manifests are YAML, and secrets may be supplied as environment-variable references inside the single configuration file so that deployments need not store credential values in source control.

## Requirements

- R1: The core event bus shall let callers subscribe a handler to an exact event type or wildcard pattern, publish an event, and inspect published event history. Event types shall be non-empty dot-delimited strings; `*` shall match exactly one segment and `**` shall match zero or more segments. For each publication, all matching handlers shall form one chain sorted by ascending declared order and then registration order. A handler result of `False` shall stop the chain. A mapping result shall be treated as a complete event replacement and shall contain valid `type`, `payload`, and `metadata` fields; it shall replace the event presented to subsequent handlers and recorded in history, while a replacement missing any required field shall fail the publication. Any other handler result shall leave the event unchanged. Each publication shall initially use an event mapping containing `type`, `payload`, and `metadata`, and caller-supplied payload and metadata shall be preserved unless a handler returns a valid complete replacement. A handler failure shall fail that publication with the event type and failing handler identifiable, without preventing a later independent publication.

- R2: The module loader shall inspect each immediate subdirectory of the configured modules directory, validate its `module.yaml`, collect its declared capabilities, and activate each enabled module with access to the shared event bus, that module's configuration, and the complete capability catalog. A manifest shall declare a unique non-empty `name`, `produces` and `consumes` lists of event patterns, and a Boolean `middleware` field; a manifest with `middleware: true` shall also declare an integer `order`. Missing, malformed, duplicate, or configuration-referenced-but-undiscovered modules shall stop startup with a diagnostic that identifies the module and invalid field. Disabled modules shall be validated and reported as discovered but shall not be activated. Adding a conforming subdirectory and enabling its manifest name in configuration shall require no edits to core source files.

- R3: The application entry point shall accept a path to one YAML configuration file, create the event bus, load modules from the configured modules directory, and keep successfully started modules active until normal shutdown. The closed set of required configuration settings shall be a non-empty `modules_directory` path, an `enabled_modules` list, and a `modules` mapping keyed by manifest name; the `brain` settings shall contain non-empty `endpoint`, `model`, and `api_key` values, and the `twitch` settings shall contain non-empty `client_id`, `client_secret`, `access_token`, `broadcaster_id`, and `bot_user_id` values. Scalar configuration values shall support environment references of the form `${NAME}`. Literal model names, LLM endpoints, Twitch channel identities, and credentials shall not be required in source code. Unreadable YAML, unresolved required environment references, missing required settings, or module startup failure shall terminate startup with a non-zero status and a diagnostic that does not disclose credential values. Normal termination shall give activated modules an opportunity to close network sessions and shall complete without an unhandled traceback.

- R4: The Twitch module shall act as both source and sink. Once enabled, it shall authenticate with configured Twitch credentials, maintain an EventSub WebSocket session for the configured broadcaster's chat-message notifications, reconnect and restore reception after a recoverable disconnect, and publish each notification whose message ID has not previously been processed during the current module activation as `channel.chat.message`; a repeated message ID during that activation, including after reconnection, shall produce no additional event. The published payload shall expose the broadcaster ID, chatter ID, chatter display name, message ID, and plain message text needed by downstream modules, while metadata shall identify Twitch as the source. When it consumes `channel.chat.send`, it shall submit the event's non-empty text to Twitch's Helix chat-message operation using the configured broadcaster and bot identities and, when present, preserve the requested parent message ID as a reply; an event whose text is missing or empty shall result in no Helix request and shall be surfaced as invalid input. Authentication rejection, EventSub subscription rejection, malformed notification data, and non-success Helix responses shall be surfaced with operation context and shall never be reported as a successful receive or send.

- R5: The brain module shall consume `channel.chat.message`, construct a model request from the incoming viewer message, available viewer/conversation context, and a system prompt derived at runtime from the loaded modules' declared `produces` and `consumes` capabilities, then call the configured OpenAI-compatible chat endpoint and model. No model, endpoint, or Twitch-specific output destination shall be embedded as an immutable source-code choice. Model output shall be split into sections introduced by `[send:<event.type>]`; for each recognized type the module is declared to produce, the non-empty section body shall be published as that event's `payload.text`, with source-message and viewer identifiers retained in metadata. Unsupported tags, empty sections, malformed responses, timeouts, transport failures, and non-success endpoint responses shall produce no outbound event and shall surface a diagnostic that excludes credentials and unrelated prompt content.

- R6: The audit module shall register as middleware for the `**` pattern at order `90` and write one structured, single-line stdout record for every event that reaches it, including at least the event type and payload. It shall return the event unchanged, shall never block the chain, and shall prevent slow or failed output writes from delaying or failing event publication. At any nesting depth, audit output shall replace the value of a field whose key case-insensitively equals `authorization`, `client_secret`, `access_token`, `api_key`, `credential`, or `credentials` with the literal `[REDACTED]`; fields with other keys, including ordinary chat content, shall retain their values.

- R7: The supplied example configuration and three manifests shall form a coherent, self-documenting deployment example: the Twitch manifest shall produce `channel.chat.message` and consume `channel.chat.send`; the brain manifest shall consume `channel.chat.message` and produce `channel.chat.send`; and the audit manifest shall consume `**`, declare middleware enabled, and declare order `90`. The example configuration shall enable all three modules, include every required setting with non-secret sample values or environment references, and be parseable without modification to its YAML structure.

## Acceptance criteria

- AC1 (R1): Given handlers registered for `channel.chat.message`, `channel.*.message`, and `**`, publishing a `channel.chat.message` event invokes all 3 handlers; publishing `channel.chat.send` invokes only the `**` handler; and publishing `channel.message` matches `channel.**` but not `channel.*.message`.

- AC2 (R1): Given matching handlers registered in this sequence at orders `90`, `10`, `10`, and `50`, one publication invokes them in the sequence `10` first-registered, `10` second-registered, `50`, `90`; if the `50` handler returns `False`, the `90` handler has 0 invocations.

- AC3 (R1): When an earlier handler returns a complete replacement containing `type`, `payload`, and `metadata`, a later handler and the corresponding `list_events()` entry observe exactly that replacement; a replacement missing any 1 of those fields fails the publication. When a handler raises, the publication reports its event type and handler, and a subsequent independent publication still completes.

- AC4 (R2): In a fixture containing 2 conforming immediate module subdirectories, both are discovered and the enabled one is activated exactly once with the same bus, its own settings, and a capability catalog containing both manifests, while the disabled one is activated 0 times.

- AC5 (R2): Each of these fixtures independently prevents activation and names the responsible module and field: absent `module.yaml`, duplicate manifest name, non-list `produces`, missing `middleware`, non-Boolean `middleware`, non-integer `order` when `middleware` is `true`, and an enabled configuration name with no discovered manifest.

- AC6 (R2): Copying a conforming fourth-party module directory beneath the configured modules directory and adding only its manifest name and settings to configuration causes it to be discovered and activated, with 0 edits to `core/` files.

- AC7 (R3): Starting with a valid configuration path activates the 3 enabled MVP modules and remains running until a termination signal; on that signal every activated module receives 1 shutdown opportunity and the process exits with status `0` without an unhandled traceback.

- AC8 (R3): Each of unreadable YAML, an unresolved required environment reference, a missing Twitch token, and a module startup exception exits non-zero before the system reports readiness; captured diagnostics name the failing setting or module and contain 0 configured credential values.

- AC9 (R3): Searching executable Python sources for the endpoint, model, broadcaster identity, bot identity, and credentials from a valid test configuration finds 0 literal occurrences, while changing those values only in YAML changes the values presented to the corresponding modules on the next start.

- AC10 (R4): Given one valid EventSub chat notification, the bus receives exactly 1 `channel.chat.message` event whose payload equals the notification's broadcaster ID, chatter ID, chatter display name, message ID, and plain text, and whose metadata identifies `twitch` as its source; replaying a notification with the same message ID during the same activation, including after reconnection, produces 0 additional chat events.

- AC11 (R4): After a simulated recoverable WebSocket disconnect, the module establishes a replacement session and restores the configured chat subscription; a valid notification on that session again produces exactly 1 chat event.

- AC12 (R4): A `channel.chat.send` event with text and a parent message ID causes exactly 1 Helix chat request using the configured broadcaster ID and bot ID, with the same text and parent ID; an event with missing or empty text causes 0 Helix requests, is reported as invalid input, and produces 0 success records.

- AC13 (R4): Simulated authentication rejection, subscription rejection, malformed notification, and non-success Helix response each produce an operation-specific failure diagnostic and 0 false success records; no diagnostic contains the configured client secret or access token.

- AC14 (R5): For an incoming viewer message and a capability catalog containing the 3 MVP manifests, the model receives exactly 1 request using the configured endpoint and model; its system context names the catalog's input and output event capabilities, and its user/conversation context retains the incoming text and viewer identifier.

- AC15 (R5): Given model output `[send:channel.chat.send]Hello there`, the brain publishes exactly 1 `channel.chat.send` event with `payload.text` equal to `Hello there` and metadata containing the source message and viewer identifiers.

- AC16 (R5): Given a response containing 2 recognized, non-empty tagged sections, the brain publishes 2 events in source order; given an unsupported tag, empty section, malformed response, timeout, transport failure, or non-success response, it publishes 0 events for the affected response and emits a diagnostic containing 0 credential values and 0 unrelated prompt messages.

- AC17 (R6): With audit configured at order `90`, publishing an event that reaches audit emits exactly 1 parseable single-line stdout record containing its type and payload, and the next handler receives an event equal to the event audit received.

- AC18 (R6): With stdout writes delayed or made to fail, publication to a downstream probe still completes and invokes the probe exactly once. For an event containing all 6 sensitive keys named in R6 across top-level and nested mappings with mixed key casing, the record contains `[REDACTED]` for all 6 values and contains 0 original secret values, while an ordinary `text` field retains its original value.

- AC19 (R7): All 3 manifests parse as YAML and declare exactly the capability directions in R7; the audit manifest declares middleware `true` and numeric order `90`.

- AC20 (R7): `config.yaml.example` parses as YAML; has a non-empty `modules_directory`; enables exactly the `twitch`, `brain`, and `audit` module names; contains a `modules` mapping with non-empty `${NAME}` references for `brain.api_key`, `twitch.client_id`, `twitch.client_secret`, and `twitch.access_token`; contains non-empty sample values or `${NAME}` references for `brain.endpoint`, `brain.model`, `twitch.broadcaster_id`, and `twitch.bot_user_id`; and contains 0 usable credential values.

- AC21 (R7): With simulated Twitch and OpenAI-compatible services and the example configuration populated through environment references, 1 Twitch chat notification traverses the Twitch source, brain, tagged `channel.chat.send` route, and Twitch sink to produce exactly 1 Helix send request, while audit records both the input and output event without blocking either.

## Caller Enumeration

The API surface is new. A repository-wide search using `rg --files` followed by symbol searches for `EventBus`, `publish`, `on`, `list_events`, and `ModuleLoader` found no existing source files or callers. Dynamic or external callers cannot be enumerated before the API exists. The planned in-repository callers required by this specification are:

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| `core/loader.py` | Module activation | Provide each activated module the shared event bus and capability catalog. |
| `core/main.py` | Application startup | Create the event bus and invoke the module loader with parsed configuration. |
| `modules/twitch/__init__.py` | Twitch source and sink lifecycle | Publish incoming chat events and subscribe to outgoing chat events. |
| `modules/brain/__init__.py` | Brain input and output pipeline | Subscribe to chat input and publish each recognized tagged output. |
| `modules/audit/__init__.py` | Audit middleware lifecycle | Subscribe to the catch-all event pattern at order `90`. |
