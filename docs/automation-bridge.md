# Automation bridge — operator guide

**English** · [Français](automation-bridge.fr.md)

**Status: normative.** This guide is the reference for the automation bridge, the optional
module `streamerbot`. Where its French translation differs, this English text wins.

**Status of the bridge: not delivered — awaiting real-instance trial.** Everything below is
proven against a fake instance that replays documented frames, never against a real one. The
[trial status](#trial-status) section says what remains to be verified.

The bridge connects the companion to a stream-automation application you already run — a
Streamer.bot instance, called *the instance* below. It is written from the study
`docs/research/streamerbot-etude-2026-10.md` and the decisions of the phase-5 brief.

## What the bridge does, and what it does not

The bridge is **optional in every case**. Not enabled, the companion is exactly what it is
without it: no setting becomes required, no default changes, and no shipped profile enables or
names it. Enabled but unable to reach the instance, the companion still reaches readiness; the
bridge reports itself degraded and its capabilities answer "not ready".

What it does, over **one** authenticated WebSocket connection that the companion opens to the
instance's own WebSocket server (the companion is the client; it opens no listening port):

- **A sink.** One write action, `automation.action.run`, runs one named action of your instance,
  behind the companion's own authorization rules, an allowlist, deadlines and honest outcomes.
- **Triggers (cues).** An event your instance deliberately routes to the companion — a *cue* —
  enters as an ordinary chat event of kind `cue`, evaluated by the trigger policies under
  `triggers.streamerbot`, and can start a brain run.
- **Replaced inputs, never parallel ones.** When the profile says so, the instance *replaces* a
  platform's chat input (the `chat` family) or viewer read (the `viewers` family). For every
  (family, platform) pair at most one module is active; the native module stops ingesting that
  pair.

What it does **not** do:

- It never reports that an instance action ran. The instance acknowledges a request; it does not
  confirm execution, so a run ends `external_unknown`, never `success`.
- It does not send chat. Replies stay with the platform modules (`twitch`, `kick`, `youtube`). A
  platform whose chat the instance reads and whose platform module is disabled has no
  `chat.write` provider: a reply there ends `no_provider`.
- It does not wait for a confirmation round trip from your actions (study question 6).
- It does not discover actions for you: only what the allowlist names is reachable.
- It does not fall back. If the instance is down, nothing else takes over the pairs it owns.
- It does not change the proxy protocol: placed in an agent process, only the run action
  reaches the brain (combination 8).

## Settings

Every setting lives under `modules.streamerbot`. Nothing in the schema is required; the
settings hook refuses, at `--check-config`, a configuration the bridge cannot use, with one
diagnostic per field that never echoes a value.

| Setting | Default | Meaning |
| --- | --- | --- |
| `url` | none (refused when missing) | The instance's WebSocket server, `ws://` or `wss://`. A non-loopback host must use `wss`; plain `ws` is accepted on a loopback address only. No address is assumed. |
| `password` | none | The server's authentication password, always as a `${NAME}` reference. A declared credential: redacted from every trace, log and diagnostic. Absent, the bridge does not authenticate, and is degraded `authentication_required` if the instance requires it. |
| `companion_name` | none (refused when missing) | The name viewers use to address the companion; the default trigger policy accepts a chat message that mentions it. |
| `deadlines.connect_seconds` | `5` | How long opening the connection may take. |
| `deadlines.authentication_seconds` | `5` | How long the hello and the authentication exchange may take. |
| `deadlines.request_seconds` | `5` | How long one request may wait for its answer (inventory, subscription, viewer list). |
| `deadlines.acknowledgement_seconds` | `10` | How long a run may wait for the instance's acknowledgement. It must stay at least 1 second below the run action's own 15-second timeout, so the bridge's cause is never pre-empted. |
| `reconnect.min_delay_seconds` | `1` | The first delay after a lost connection. |
| `reconnect.max_delay_seconds` | `60` | The cap every reconnection delay stays under. |
| `queue_max_events` | `256` | How many inbound events wait for the consumer. An event arriving at a full queue is dropped and counted. |
| `allowlist` | empty | The instance actions the run action may reach (next section). Empty or absent, every run is refused. |
| `companion_identities` | empty | The companion's own account id per platform, e.g. `{twitch: ${TWITCH_BOT_USER_ID}}`. Required for every platform whose chat the bridge owns (anti-echo). |
| `instance_identity` | empty | The broadcaster account id per platform: the channel a cue without one is attributed to, and the only channel the bridge's viewer read answers for. |
| `trust_instance_roles` | `false` | Whether role claims reported by the instance (moderator, subscriber…) satisfy `audience` rules. |

Every deadline and delay is a finite positive number of seconds; `queue_max_events` is a positive
integer; `reconnect.min_delay_seconds` must not exceed `reconnect.max_delay_seconds`. An identity
must not contain `:`. The reserved `limits` block every module receives is accepted and not read.

## The allowlist

The instance authorizes nobody: whoever can talk to its server can run any of its actions. The
allowlist is the companion's own restriction on top of it.

- Each entry names **exactly one** instance action, `{action: <name>}`, or **one** instance
  group, `{group: <name>}`, as the instance shows them.
- An entry may carry an `arguments` schema (the same schema subset as the module settings). An
  entry without one accepts only an empty `arguments` object.
- Two `action:` entries with the same name, or two `group:` entries with the same name, are
  refused by the settings hook.
- When several entries match one instance action, its `action:` entry's schema alone decides if
  one names it; otherwise the arguments must satisfy the schema of every matching `group:` entry.
- **A group entry delegates the choice of reachable actions to whoever edits that group in the
  instance.** Anyone who can add an action to that group in the instance makes it reachable by
  the companion, with no change to your profile. Prefer `action:` entries; use a group only for a
  group you alone edit.
- The companion's authorization rules still apply first, unchanged: a run needs an `actions:`
  rule granting `automation.run` on the scope `automation`, or it is refused `not_authorized`
  before the bridge sees it.
- At every successful connection the bridge reads the instance's inventory and reconciles the
  allowlist against it. The reachable set is every configured action name, plus every action of a
  configured group, that exists exactly once in the inventory and is enabled. One value-free
  trace, `automation.allowlist.reconciled`, lists the entries that reach nothing, by their
  configured name. An inventory that cannot be read leaves the bridge not ready
  (`inventory_unavailable`).

Every refusal of the allowlist, and every argument violation, is decided before any frame
leaves the process.

```yaml
modules:
  streamerbot:
    allowlist:
      - action: BRB
      - action: Shoutout
        arguments:
          type: object
          properties:
            user:
              type: string
          additionalProperties: false
```

## Input ownership — no fallback

Three input families exist, defined by the kernel and named after no vendor:

- `chat` — chat messages and platform notices (subscriptions, raids, follows, tips…) of one
  platform;
- `viewers` — the viewer read `users.read` of one platform;
- `cues` — events an operator automation deliberately routes to the companion.

A module declares, in its manifest, which (family, platform) pairs it can serve and whether it
serves them by default. The bridge declares `chat` and `viewers` for `twitch`, `kick` and
`youtube` **not** by default, and `cues` on every platform (`*`) by default. So, enabled with no
`inputs` block, the bridge owns cues only, and the native modules keep their chat and viewers.

The optional top-level `inputs` block decides a pair explicitly: family → platform (or `'*'`) →
owner, where the owner is one enabled module name or the literal `none` (deliberately unowned).

```yaml
inputs:
  chat:
    twitch: streamerbot     # the instance reads twitch chat; the twitch module stops ingesting it
  cues:
    '*': none               # no cue reaches the companion
```

Resolution, for one pair: the explicit `inputs.F.P` entry; else `inputs.F.'*'`; else the single
enabled module declaring the pair by default; else the pair is unowned. Two default declarers,
an owner list of two modules, an owner that is not enabled or does not declare the pair, and a
family outside the vocabulary are each refused at preparation — by `run` and `--check-config`
alike, before any module is prepared and before any socket opens — with a diagnostic naming the
family, the platform and the providers or the value.

At runtime, an event a module publishes for a pair it does not own reaches no subscriber, is
counted in `input_ownership_rejections`, and is diagnosed once per (module, family, platform) by
the trace `input.ownership.rejected`. A platform module that does not own its chat pair opens no
ingestion for it but keeps `chat.write` and its readiness.

**There is no fallback.** When the bridge owns a pair and the instance is down, nothing is
ingested for that pair and nothing takes it over: the bridge's `module.degraded` record is the
visible consequence. To go back to a native input, change the profile.

The configuration UI shows the resolved table read-only on the core-settings page — owner,
origin (explicit or default rule) and every candidate — and its Check reports the ownership
diagnostics. Ownership is changed in the profile only.

## Run outcomes

The run action `automation.action.run` takes `{action: <name>, arguments: {…}}`: `action` is
required (1 to 128 characters), `arguments` is optional and no other key is accepted. It is a
`write` with `idempotency: none`, permission `automation.run`, one wildcard destination of scope
`automation`, not model-proposable, and an effect-only delivery (`text_argument: none`), so a
route can use it as a fixed delivery.

Its outcomes are exactly these. None of them says that the instance action ran, because the
instance never says so; every one carries `retryable: false`, and the run is never retried.

| Situation | Status | Code | Cause | What the companion knows |
| --- | --- | --- | --- | --- |
| The name or group is not in the allowlist | `refused` | `not_allowlisted` | — | Nothing was sent. |
| Allowlisted, absent from the instance inventory | `refused` | `instance_action_missing` | — | Nothing was sent. |
| Allowlisted name matching two or more instance actions | `refused` | `instance_action_ambiguous` | — | Nothing was sent. |
| The instance action is disabled in the inventory | `refused` | `instance_action_disabled` | — | Nothing was sent. |
| `arguments` violate the entry's schema | `error` | `invalid_arguments` | — | Nothing was sent. |
| The bridge is not ready | `refused` | `provider_not_ready` | — | Nothing was sent. |
| The instance acknowledged the request | `external_unknown` | `external_effect_unknown` | `execution_unconfirmed` | The request was accepted; whether the action ran is unknown. |
| The instance answered an error | `error` | `provider_failed` | `instance_rejected` | The instance refused the request. |
| Request sent, then no acknowledgement before the deadline, or the connection lost or the call cancelled | `external_unknown` | `external_effect_unknown` | `acknowledgement_lost` | The request may have reached the instance; whether the action ran is unknown. |
| Deadline or cancellation before the frame left the process | `timeout` / `cancelled` | existing codes | — | Nothing was sent. |

The model and the operator both see these outcomes: the observation returns into the brain's
loop and the outcome is in the trace. A request is sent at most once per call.

## Connection, readiness and lost events

- **Readiness.** The bridge is ready only while it is connected, authenticated (with the
  instance's documented salt and challenge scheme, when the instance requires it) and holding a
  reconciled inventory. Before that, the run action and the bridge's `users.read` are refused
  `provider_not_ready` with nothing sent.
- **Startup never fails because of the instance.** An unreachable instance, a refused password,
  a missing password the instance requires, a malformed hello or an unreadable inventory yields a
  ready companion and one `module.degraded` record for the bridge, with the value-free reason
  `unreachable`, `authentication_failed`, `authentication_required`, `protocol_error` or
  `inventory_unavailable`. No password, URL or frame text appears in any trace or log.
- **A lost connection** makes the bridge not ready at once (one `module.degraded`). It reconnects
  with a bounded backoff, from `reconnect.min_delay_seconds` up to `reconnect.max_delay_seconds`;
  a reconnection that succeeds reports `module.ready` again — never silently.
- **Events while disconnected are lost.** The instance does not replay what it published while
  the bridge was not connected: a chat message, a notice or a cue sent during an outage never
  reaches the companion. Nothing is reconstructed after reconnection.
- **Subscriptions.** The bridge subscribes only to the event categories the pairs it owns need:
  the custom-event category for cues, and, for each owned (`chat`, P), P's chat-message and
  notice categories. An instance event with no documented mapping is dropped and counted.
- **Bounded queue.** Inbound events wait in a queue of `queue_max_events`; an event arriving at a
  full queue is dropped and counted (`dropped_events`), never blocking the connection.
- **Shutdown** closes the connection within the companion's close budget; a run in flight ends
  with the outcome of its state in the table above.

## Cues — the recipe

A cue is how your instance says "react to this now": a channel point redeem, a hotkey, a timer,
any of the instance's triggers. In the instance, end the action that should reach the companion
with the sub-action *Trigger Custom Event* with *Use Args* checked, and set these arguments before
it:

| Argument | Required | Rule |
| --- | --- | --- |
| `cue_id` | yes | Non-empty, at most 128 characters, new for each cue: a repeated id inside the dedup window is treated as the same cue. |
| `platform` | yes | The platform the cue is about, e.g. `twitch`. |
| `channel_id` | no | Defaults to `instance_identity` of that platform; with neither, the cue is dropped. |
| `user_id`, `user_name` | no | The viewer the cue is about. Absent, the author is one stable instance identity, distinct from every viewer and every companion identity. |
| `text` | yes | A string, empty allowed, at most 500 code points; what a route's `command` matches. Absent or not a string, the cue is dropped. |

The bridge turns it into one `channel.chat.message` with `payload.kind: cue` and
`payload.message_id` equal to `cue_id`. A malformed or oversized envelope is dropped and counted,
never truncated or repaired.

The default policy under `triggers.streamerbot` accepts every cue. A brain route then decides
what happens; a route matching only cues may close the run with the run action, like a route on
platform notices:

```yaml
modules:
  brain:
    routes:
      - name: brb
        match:
          kinds: [cue]
          command: "!brb"
        instructions: "The streamer steps away: say so in one short sentence."
        delivery:
          mode: fixed
          actions:
            - action: automation.action.run
              text_argument: none
              arguments:
                action: BRB
```

A cue that no route matches is handled like any accepted chat event, under the brain's default
instructions and delivery. A cue is not recorded in the chat context, `users` never records its
author, and `moderation` and `viewer_memory` act on ordinary messages only.

## Trust model

- **The instance is yours.** The bridge trusts the instance because it is the operator's own,
  reached over an authenticated connection (the cue assumption, A5). That is why a route matching
  only cues may hold an effect delivery, like a route on platform notices, without an `audience`
  rule.
- **Anyone able to edit the instance's actions can trigger these routes.** A cue route runs
  whatever its delivery says when a cue with its command arrives; whoever can add a *Trigger
  Custom Event* to an action of the instance — a co-moderator with access to it, an imported
  action package — can start it. Treat edit access to the instance as access to every cue route.
- **Anyone able to edit an allowlisted group chooses what the run action reaches** (see the
  allowlist).
- **Text never grants a role.** A cue's text is matched by `command`, never read as a role claim.
  Role claims of mapped chat events count only with `trust_instance_roles: true`, and their
  provenance names the bridge.
- **Anti-echo.** The companion identities are handed to the trigger engine, which refuses what
  the companion authored. A wrong identity — a viewer's id instead of the companion's — would
  silence that viewer: check `companion_identities` against the platform's own ids.
- **The password** is a declared credential, referenced as `${NAME}`; it never reaches a
  response, a log line or a diagnostic. Use `wss://` for any instance that is not on the
  companion's own machine.

## Trial status

Not delivered — awaiting real-instance trial. The fake-instance tests replay frames whose shape
the instance's documentation states; no test asserts an instance behaviour the study lists as
unverified. Eight opt-in trial nodes, `tests/test_phase5_trials.py`, one per study question, are
skipped unless their variables are set:

1. how the instance answers a request naming a nonexistent action;
2. latency, and whether its request queue blocks;
3. authentication in enforce mode;
4. the minimum version per request;
5. C# HTTP/JSON availability (an operator observation);
6. the custom-event round trip;
7. the servers under Wine (an operator observation);
8. the volume of subscribed events.

No refusal of the bridge depends on the answer to question 1. Until the trial record
`docs/campaigns/phase5/bridge-trial-record.md` exists and answers the eight questions with the
instance version and the date, this guide, the README and the documentation index state the
bridge as not delivered.

## Combinations

Every setup is configuration alone. Each combination below gives the fragment to apply to the
named shipped profile, and what is enabled, disabled and refused. Rows 9 to 12 are refused by
`--check-config`; their diagnostic is quoted under the fragment.

**Applying a fragment.** A mapping merges key by key into the profile's mapping; a list extends
the profile's list of the same key (its items are appended after the existing ones); any other
value replaces the profile's. Then export every `${NAME}` the result references and run
`python -m core.main --config <profile> --check-config`. A fragment is not an overlay as is: an
overlay replaces a list wholesale, so an overlay must carry the complete lists.

| # | Setup | Enabled | Disabled | Refused |
| --- | --- | --- | --- | --- |
| 1 | Instance absent: the bridge not enabled (`config.yaml.example`) | Everything the shipped profile enables, unchanged | The bridge: no setting, no socket, no `inputs` block | A call to `automation.action.run`: no enabled module declares it (`unknown_action`) |
| 2 | Same machine, sink only (`config.yaml.example`) | The bridge's run action, granted to the brain; native chat and viewers unchanged | Cues: `inputs.cues.'*'` is `none` | A run outside the allowlist (`not_allowlisted`); every cue (no owner) |
| 3 | Same machine, sink and cues (`config.yaml.example`) | The run action; cues on every platform, evaluated under `triggers.streamerbot`; the `brb` cue route | Nothing native: twitch chat and `users` keep their pairs | A run outside the allowlist; a route on `message` holding the run action without an `audience` (refused at prepare) |
| 4 | Same machine, the instance reads Twitch chat (`config.yaml.example`) | Twitch chat and notices through the instance, evaluated under `triggers.streamerbot`; `chat.write` stays with the `twitch` module | The `twitch` module's chat ingestion (no EventSub subscription) | A twitch chat event the `twitch` module publishes (dropped and counted); any takeover while the instance is down |
| 5 | Several platforms: the instance owns Kick chat, the native `twitch` module Twitch chat (`config.yaml.example`) | Kick chat through the instance; Twitch chat through the `twitch` module | A native Kick input (the `kick` module stays disabled) | A reply on Kick: no `chat.write` provider there (`no_provider`); a Kick event from any other module |
| 6 | The instance owns `viewers` for Twitch (`config.server.yaml.example`) | `users.read` on Twitch answered from the instance's active viewers (`active_viewers`, `complete: false`); `users` serves the other platforms | The `users` module's binding for Twitch | `users.read` on Twitch for any channel but `instance_identity.twitch` (`unsupported_destination`) |
| 7 | Remote brain: the bridge on the brain host over `wss://` (`config.server.yaml.example`) | The bridge dialing the instance through a TLS front (for example the instance's Tailscale Serve recipe); the run action and cues on the brain host | Plain `ws://` to the instance host | A plain `ws://` URL to that host (combination 11); an unreachable host is no refusal: a ready companion, the bridge degraded `unreachable` |
| 8 | The bridge in the agent process, sink only, reached through the proxy's actions allowlist (`agent.yaml.example`) | The run action, lent to the brain by `agent_link` and served on the brain host by the `proxy` module's actions allowlist | Families and cues do not reach the brain in this placement: proxy protocol v1 carries only the `agent.status` event, so `inputs.cues.'*'` is `none` | A run the brain host's or the agent's rules do not grant (`not_authorized`); a run outside the allowlist (`not_allowlisted`) |
| 9 | Refused: two owners for one pair (`config.yaml.example`) | Nothing: the profile is refused | Every module: none is prepared | `inputs.chat.twitch` naming two modules |
| 10 | Refused: an owner that is not enabled (`config.yaml.example`) | Nothing: the profile is refused | Every module: none is prepared | `inputs.chat.twitch: streamerbot` without the bridge in `enabled_modules` |
| 11 | Refused: a non-loopback `ws://` URL (`config.yaml.example`) | Nothing: the profile is refused | Every module: none is prepared | `url` with plain `ws` to a non-loopback host |
| 12 | Refused: the bridge owning a chat pair without a companion identity for it (`config.yaml.example`) | Nothing: the profile is refused | Every module: none is prepared | (`chat`, twitch) owned by the bridge with no `companion_identities.twitch` |

### Combination 1 — instance absent

The bridge is not enabled: there is nothing to add.

<!-- combination 1 base=config.yaml.example -->
```yaml
{}
```

### Combination 2 — same machine, sink only

<!-- combination 2 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    allowlist:
      - action: BRB
inputs:
  cues:
    '*': none
actions:
  - rule_id: brain-runs-instance-actions
    action_name: automation.action.run
    destination:
      platform: "*"
      channel_id: "*"
      scope: automation
    principals:
      - brain
    natures:
      - write
    granted_permissions:
      - automation.run
```

### Combination 3 — same machine, sink and cues

<!-- combination 3 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    instance_identity:
      twitch: ${TWITCH_BROADCASTER_ID}
    allowlist:
      - action: BRB
  brain:
    routes:
      - name: brb
        match:
          kinds: [cue]
          command: "!brb"
        instructions: "The streamer steps away: say so in one short sentence."
        delivery:
          mode: fixed
          actions:
            - action: automation.action.run
              text_argument: none
              arguments:
                action: BRB
actions:
  - rule_id: brain-runs-instance-actions
    action_name: automation.action.run
    destination:
      platform: "*"
      channel_id: "*"
      scope: automation
    principals:
      - brain
    natures:
      - write
    granted_permissions:
      - automation.run
```

### Combination 4 — the instance reads Twitch chat

The `twitch` module stays enabled for `chat.write`; its chat ingestion stops.

<!-- combination 4 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    companion_identities:
      twitch: ${TWITCH_BOT_USER_ID}
    instance_identity:
      twitch: ${TWITCH_BROADCASTER_ID}
inputs:
  chat:
    twitch: streamerbot
```

### Combination 5 — several platforms

The instance reads Kick chat; the native `twitch` module keeps Twitch chat by its default rule.

<!-- combination 5 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    companion_identities:
      kick: ${KICK_BOT_USER_ID}
    instance_identity:
      kick: ${KICK_CHANNEL_ID}
inputs:
  chat:
    kick: streamerbot
```

### Combination 6 — the instance owns Twitch viewers

<!-- combination 6 base=config.server.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    instance_identity:
      twitch: ${TWITCH_BROADCASTER_ID}
inputs:
  viewers:
    twitch: streamerbot
```

### Combination 7 — remote brain over `wss://`

The bridge runs on the brain host and dials the instance through a TLS front; replace the host
with yours. `--check-config` opens nothing, so it checks without a reachable host.

<!-- combination 7 base=config.server.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: wss://instance-host.example/
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    instance_identity:
      twitch: ${TWITCH_BROADCASTER_ID}
    allowlist:
      - action: BRB
actions:
  - rule_id: brain-runs-instance-actions
    action_name: automation.action.run
    destination:
      platform: "*"
      channel_id: "*"
      scope: automation
    principals:
      - brain
    natures:
      - write
    granted_permissions:
      - automation.run
```

### Combination 8 — the bridge in the agent process, sink only

On the streaming PC, the agent serves the run action and `agent_link` lends it to the brain. On
the brain host, append `automation.action.run` to `modules.proxy.actions` of
`config.server.yaml.example` and grant it there with the same `actions:` rule: the proxy finds
the run action's specification in the catalog of discovered manifests, the bridge disabled on
that host included. Families and cues do not reach the brain in this placement, so the fragment
sets cues to `none`.

<!-- combination 8 base=agent.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    allowlist:
      - action: BRB
  agent_link:
    actions:
      - automation.action.run
inputs:
  cues:
    '*': none
actions:
  - rule_id: brain-runs-instance-actions
    action_name: automation.action.run
    destination:
      platform: "*"
      channel_id: "*"
      scope: automation
    principals:
      - brain
    natures:
      - write
    granted_permissions:
      - automation.run
```

### Combination 9 — refused: two owners for one pair

<!-- combination 9 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    companion_name: Companion
    companion_identities:
      twitch: ${TWITCH_BOT_USER_ID}
inputs:
  chat:
    twitch: [twitch, streamerbot]
```

Diagnostic: `inputs: family 'chat': platform 'twitch': names more than one owner`

### Combination 10 — refused: an owner that is not enabled

<!-- combination 10 base=config.yaml.example -->
```yaml
inputs:
  chat:
    twitch: streamerbot
```

Diagnostic: `inputs: family 'chat': platform 'twitch': owner 'streamerbot' is not enabled`

### Combination 11 — refused: a non-loopback `ws://` URL

<!-- combination 11 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ws://192.0.2.10/
    companion_name: Companion
```

Diagnostic: `module 'streamerbot': field 'url': must use wss unless the host is a loopback address`

### Combination 12 — refused: owned chat without a companion identity

<!-- combination 12 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    companion_name: Companion
inputs:
  chat:
    twitch: streamerbot
```

Diagnostic: `module 'streamerbot': field 'companion_identities.twitch': is required: this module owns the chat input of platform twitch`
