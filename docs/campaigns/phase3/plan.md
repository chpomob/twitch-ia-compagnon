---
spec: "phase3-presence-memory-platforms"
version: "1.0"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: true
---

# Implementation Plan — Phase 3, presence, viewer memory, moderation and platforms

Planned from `spec.md` (`phase3-presence-memory-platforms` v1.0: R1–R8,
AC1–AC42). Revision 1 addresses the plan-review findings in
`findings.json` (`findings-input: true`): F-P1 absent delivery (P13), F-P2
moderation concurrency (P14, P15), F-P3 kick `chat.write` catalog assertion
(P19) and F-P4 the memory argument contracts (decision 6, P11). The table
"Review findings addressed" before the ordering rationale maps each one. Step ids run
`P1`…`P26` with no gaps. Every `Files:` entry is a spec target or lies under
`docs/campaigns/phase3/`, the permitted scope the spec states.

## Reading of the current tree (what the steps build on)

- `core/contracts.py`: `ActionSpec` (line 1016) with `nature` in
  `ACTION_NATURES = {read, write}`, `delivery` validated by
  `_validate_delivery` (line 1131); `ActionCall` carries `conversation_id`,
  `run_id`, `principal`, `destination` and `deadline`, with no viewer field;
  `SessionKey` (line 889) has `serialize()`; there is no event-kind vocabulary
  today.
- `core/triggers.py`: `BUILTIN_TRIGGER_TYPES` holds `probability`,
  `audience` and `keyword` (line 183); `_normalize` (line 990) checks a
  schema-2 envelope (`metadata.schema_version`, `metadata.source`,
  `payload.platform`/`channel_id`/`message_id`/`author.id`);
  `_evaluate_rule` (line 1092) dispatches explicitly and raises on a built-in
  that has no evaluator; `_validate_builtin_rule` (line 1204).
- `core/loader.py`: `_ACTION_KEYS` (line 185) refuses unknown keys in
  `_manifest_actions` (line 1713, `unknown = sorted(set(entry) - _ACTION_KEYS)`).
- `core/context.py`: `ChatEntry(author_id, message_id, text)`. The chat
  context keeps **no role claims**, so a consumer that needs a message
  author's roles (moderation) must index them itself.
- `core/runtime.py`: `ServiceRegistry.publish(kind, platform, service, *,
  module)` (line 233), the `ModuleServices` facade with `available`,
  `resolve` and `entries`. Phase 2 publishes only `(poll, twitch)` and
  `(poll, fake)`.
- `core/actions.py`: `ActionInvocation.mark_emitted` (line 407). An emitted
  call interrupted by its deadline ends `external_unknown`, never `timeout`
  (line 289). Phase 2 R10 adopts a late provider-authored interruption record.
- `modules/brain/__init__.py`: `PRINCIPAL = "brain"` is used for every offer
  (`_offered_tools`, line 3093) and call (`_delivery_call`, line 3206);
  `_system_prompt` (line 3064) is a constant; `_delivery_for(platform,
  channel_id)` (line 2250) resolves one list per destination; `_deliver_all`
  (line 3138); `validate_settings` (line 461) with `_validate_delivery_list` /
  `_validate_delivery_entry`.
- `modules/twitch/__init__.py`: one EventSub subscription
  `channel.chat.message` (`_CHAT_EVENT`, line 153), publication at line 1661,
  `self._scheduler.admit(session_key, work)` at line 1751,
  `POLL_SERVICE_KIND = "poll"` (line 183), `_normalize_notification`
  (line 1958).
- `tests/conftest.py`: `ManualClock`, `ScriptedModel`, `ScriptedPollService`,
  `runtime_context`, `settle`, `wait_until`, `events_of`, `trace_texts`.
- `tests/test_main.py::test_pyproject_ships_core_and_modules_with_every_manifest`
  requires a `"modules.<name>" = ["module.yaml"]` package-data line for every
  directory holding a `module.yaml`. So **each step that creates a module
  adds its own `pyproject.toml` line**. `pyproject.toml` is a spec target,
  so this is in scope.

## Decisions the plan takes (each one is a contract the steps implement)

1. **Where the event kind lives.** A normalized event carries an optional
   `payload.kind`. When it is absent the kind is `message`, so every phase 2
   event keeps its meaning. When it is present it must be one of
   `EVENT_KINDS`, or the event is refused as `invalid_payload:payload.kind`.
   `core/contracts.py` owns the vocabulary and `validate_event_kind`;
   `core/triggers.py` reads it in `_normalize`.
2. **Notices travel as chat events.** A community notice is published on the
   existing `channel.chat.message` type with `payload.kind` set. It goes to
   the chat context through the same feed call as a message, and the users
   directory sees it too. Neither `modules/chat_context/` nor `modules/users/`
   is a target, so the notice must fit the existing event shape.
   Consequently, every command consumer that is new in phase 3 acts **only
   on `kind == message`**: moderation approvals, the memory `!forgetme`
   command and the watch `!watch`/`!unwatch` commands. A notice's system text
   can never trigger one of them.
3. **Anonymous authors.** A notice with no trusted author (an anonymous gift)
   is fed to the chat context under the reserved author identity
   `system:anonymous`. It is never trigger-evaluated or admitted. Platform
   modules refuse any platform author id containing `:` (R6), so no real
   viewer can hold that identity or `system:watch`.
4. **Run principal.** The brain derives the principal of a run from the
   triggering event: kind `watch_tick` → `brain.watch`; anything else →
   `brain`. The principal is fixed for the whole run: offers, model-proposed
   calls, delivery entries and the post-delivery `memory.record`.
5. **Model-proposable writes.** `ActionSpec.model_proposable: bool = False`.
   Construction refuses it on `nature == "read"` and on a spec that has a
   `delivery` capability. The brain offers a write only when the flag is set
   **and** the authorized view for the run's principal and destination
   grants it. The one-per-run limit is generic: the brain executes at most
   one call of a given model-proposable action per run, and returns a second
   to the model as a `refused` observation with code `run_limit` without
   invoking the executor. This avoids naming a module in the brain. The
   executor still re-checks every call.
6. **Viewer of a memory call.** `viewer_memory` parses the call's
   `conversation_id` (the brain passes `SessionKey.serialize()`) back into
   `(platform, channel_id, viewer_id)`. A viewer id in the reserved `system:`
   namespace (for example `system:watch`), or an unparsable id, yields
   `error no_viewer`. No argument of either action names a viewer. The two
   argument contracts differ:
   - `memory.recall` takes no arguments: its schema is `{}` with
     `additionalProperties: false`;
   - `memory.record` accepts only the exchange data the brain supplies
     (`viewer_text`, `reply_text`, `delivery`, `display_name`, see P11),
     also with `additionalProperties: false`, so an identity argument such
     as `viewer_id`, `platform` or `channel_id` is refused by the
     executor's argument validation before the provider runs.
7. **Moderation targets.** `moderation` consumes `channel.chat.message`. It
   keeps a bounded per-channel index `message_id → (author_id,
   trusted_roles)` of `kind == message` events, capped by the chat context's
   own count bound. `target_unknown` holds when the id is absent from the
   channel's retained chat context **or** from that index.
   `target_protected` reads the indexed roles and the companion identity.
8. **Clip confirmation cadence.** After the create request is accepted, the
   first lookup is issued at once and then every 1 s on the injected clock.
   No lookup is issued after `acceptance + 15 s`. The module marks the call
   emitted (`mark_emitted`) before the create request leaves. An expiry
   after emission therefore ends `external_unknown` (the executor's existing
   rule), and an expiry before emission ends `timeout`. The module never
   subtracts a lead from the deadline (phase 2 R10 convention).
9. **Kick signatures without new dependencies.** RSASSA-PKCS1-v1_5/SHA-256
   verification uses `pow(sig, e, n)`, an EMSA encoding comparison with the
   SHA-256 DigestInfo prefix, `hmac.compare_digest`, and a minimal DER reader
   for a PEM `SubjectPublicKeyInfo`. It uses the standard library only.
10. **YouTube quota day without tzdata.** The reset instant 00:00
    America/Los_Angeles is computed from the US DST rule (second Sunday of
    March 02:00 → first Sunday of November 02:00, UTC−7/UTC−8) with integer
    arithmetic on the injected clock. The stdlib `zoneinfo` needs a tz
    database that a Windows install lacks without the third-party `tzdata`
    package, which would be a new dependency.
11. **Suite rule.** Every step ends with the full suite green. A step that
    breaks one of the six allowlisted tests rewrites **only that assertion**,
    to the running catalog value its change produces. P23 pins the final
    phase 3 values (17 manifests, 13 action names, 3 `chat.write`
    declarations, 17 package-data lines). No other test may be weakened.

## Steps

### P1: Event-kind vocabulary, `kind` validation and `model_proposable` in `core/contracts.py` (R1, R5, R6; AC25-core, AC39-core)
- **Files:** [core/contracts.py, tests/test_contracts.py]
- **Description:**
  - **Vocabulary.** Add
    `EVENT_KINDS = ("message", "sub", "resub", "sub_gift", "community_sub_gift", "raid", "follow", "tip", "watch_tick")`
    as an ordered tuple, plus a frozenset view.
  - **Named subsets.** Add `EVENT_KIND_DEFAULT = "message"` and
    `PLATFORM_NOTICE_KINDS`, which is every kind except `message` and
    `watch_tick` (R1 route safety).
  - **Validation.** Add `validate_event_kind(value, field)`: `None` → the
    default; a non-member or a non-string raises
    `ContractError(field, ...)`.
  - **Action spec flag.** Add `model_proposable: bool = False` to
    `ActionSpec`. `__post_init__` refuses a non-bool value. It also refuses
    `True` with `nature == "read"` (message "a read action is never
    model-proposable") and `True` with a delivery capability (message "a
    delivery-capable action is never model-proposable").
  - **Scope.** No platform, vendor or module name appears in either list.
- **Dependencies:** [] (none — can run first)
- **Tests:**
  - The vocabulary equals the nine names, and `PLATFORM_NOTICE_KINDS` has 7.
  - `validate_event_kind` accepts each kind and `None` (→ `message`), and
    refuses `"Raid"`, `""`, `1` and `"announcement"`.
  - `ActionSpec(... model_proposable=True, nature="read")` raises, and so
    does `True` with a `delivery` block (AC25 core half).
  - A write without delivery accepts `True`; `model_proposable="yes"` raises.
  - The default is `False` on every existing construction.
  - A word scan of `core/contracts.py` finds 0 occurrences of `kick`,
    `youtube` and `twitch` (AC39 core half; the full scan runs in P26).
- **Risks:** `ActionSpec` is frozen and may be serialized or compared
  elsewhere (proxy lending, capability view). Mitigation: add the field last,
  with a default. The step greps every `ActionSpec(` construction and every
  `dataclasses.asdict`/field iteration over it (see the caller table), so the
  proxy's spec equality still holds for specs with the default.

### P2: Built-in trigger type `event_kind` in `core/triggers.py` (R1, R6; AC3/AC5 policy prerequisites, AC29 default policy)
- **Files:** [core/triggers.py, tests/test_triggers.py]
- **Description:**
  - **Declaration.** Add `TRIGGER_TYPE_EVENT_KIND = "event_kind"` to
    `BUILTIN_TRIGGER_TYPES`. Its canonical schema is `{kinds: array, items:
    enum EVENT_KINDS, minItems: 1, uniqueItems: true}` with
    `additionalProperties: false`.
  - **Normalization.** `_normalize` reads `payload.kind` through
    `validate_event_kind`. An invalid value is refused
    `invalid_payload:payload.kind` with the identifiers already established.
    The kind joins `_Normalized`.
  - **Evaluation.** Add `_evaluate_event_kind(parameters, kind)`, a pure
    membership test that is never handed the text or the RNG, so it makes
    0 draws. Wire it into the explicit `_evaluate_rule` dispatch and into
    `_validate_builtin_rule`.
  - **Keyword rule.** An event whose text is empty or absent never satisfies
    a keyword rule, even for a mention-of-companion default. Verify the
    current `_evaluate_keyword` behaviour on `""`, and make it explicit if it
    is not already so.
  - **Composition.** The existing combination operators (`all_of`, `any_of`,
    `none_of`) compose the new type unchanged.
- **Dependencies:** [P1]
- **Tests:** (new cases in `tests/test_triggers.py`, all with a counting RNG
  double)
  - `event_kind [raid]` accepts a raid event and rejects a message and an
    event without `kind`, with 0 draws each time.
  - `any_of: [keyword !ask, event_kind [raid, sub]]` accepts `!ask x` and a
    `sub` notice with empty text, and rejects a plain message.
  - `all_of: [audience moderators, event_kind [message]]` accepts a trusted
    moderator message and rejects the same text from an untrusted author.
  - Refusals: a policy naming `kinds: [announcement]`, `kinds: []` and
    `kinds: [raid, raid]` is refused at registration naming the field.
  - A keyword rule on a `follow` notice with empty text rejects.
  - An event with `payload.kind: "bogus"` is refused as invalid payload.
  - The existing tests that iterate `BUILTIN_TRIGGER_TYPES` filter by name
    and stay unchanged.
- **Risks:** a module that declares `event_kind` in its manifest before this
  step lands would fail discovery, so manifests only declare it from P4 on.
  The dispatch must not read the kind for the other three types, or
  determinism tests pinned on draw counts could shift.

### P3: Loader accepts `model_proposable` (R5; AC25 discovery half)
- **Files:** [core/loader.py, tests/test_contracts.py]
- **Description:**
  - **Key.** Add `"model_proposable"` to `_ACTION_KEYS`. `_manifest_actions`
    passes it to `ActionSpec` (absent → `False`).
  - **Diagnostics.** A `ContractError` raised by the spec is already mapped
    through `_action_field`. The step checks that the diagnostic names the
    module directory **and** the action name, and adds the action name if it
    does not.
  - **Other keys.** Every other unknown key is still refused as today.
- **Dependencies:** [P1]
- **Tests:** (in `tests/test_contracts.py`, which the spec targets for these
  refusals, using a temporary modules root and `ModuleLoader` discovery)
  - A manifest with `model_proposable: true` on a `read` action fails
    discovery, and the message names the module and the action.
  - `model_proposable: true` on a delivery-capable write fails the same way.
  - `model_proposable: true` on a delivery-less write is discovered, and its
    spec has `model_proposable is True`.
  - An unknown key `foo: 1` is still refused.
  - Every shipped manifest still discovers with `model_proposable is False`.
- **Risks:** `tests/test_loader.py` is not a target, so no loader test may
  need editing. Mitigation: the change is additive; run `tests/test_loader.py`
  unchanged.

### P4: Fixture platform — notices with a kind, `event_kind`, scripted `clip`/`moderation` services, `:` identity refusal; shared clip/moderation/memory doubles (R1, R2, R5, R6; AC1/AC3/AC8 support)
- **Files:** [tests/fixtures/modules/fakeplatform/__init__.py, tests/fixtures/modules/fakeplatform/module.yaml, tests/conftest.py, tests/test_triggers.py]
- **Description:**
  - **`tests/conftest.py`** gains three doubles:
    - `ScriptedClipService`: a scripted queue of create outcomes (`accepted
      <id>`, `offline`, `auth`, `rate`, `lost`, `server_error`, `hold`) and
      lookup outcomes (`found <url>`, `empty`). It keeps a request log with
      clock stamps, counts creates, and records overlap (the in-flight count
      never exceeds 1).
    - `ScriptedModerationService`: `operations` (default `{delete_message,
      timeout}`), scripted outcomes (`ok`, `rejected`, `rate`, `lost`,
      `server_error`), a request log, and the platform's duration rounding
      hook.
    - `memory_directory(tmp_path)`: a helper returning a fresh directory
      plus a function that lists `*.json` names.
  - **Fixture platform.**
    - The manifest declares `event_kind` among its trigger types and the
      optional settings `notices.kinds` and `services` (a list of
      `poll`/`clip`/`moderation`, default all three).
    - The module accepts scripted notices `{kind, author|None, text}`.
      Notices are published as `channel.chat.message` with `payload.kind`
      (decision 2) and fed to the chat context. They are trigger-evaluated
      and admitted like messages, except that an authorless notice is fed
      under `system:anonymous` and never admitted (decision 3).
    - The module refuses (and counts as invalid) any author id containing
      `:`.
    - It publishes the scripted `clip` and `moderation` services for `fake`,
      only when `context.services.available` and only the kinds listed in
      `services`.
- **Dependencies:** [P2]
- **Tests:** (`tests/test_triggers.py`, end to end through the fixture
  platform on a `runtime_context`)
  - A scripted `raid` notice under policy `event_kind [raid]` gives 1
    admission and 1 chat-context entry.
  - An authorless `sub_gift` gives 1 chat-context entry and 0 admissions.
  - An author `a:b` gives 0 admissions and invalid counter + 1.
  - With `services: [poll]`, the registry holds only `(poll, fake)`.
  - Every existing fixture-platform test (`tests/test_loader.py`,
    `tests/test_stream_control.py`) still passes: the default publishes
    `poll` as before, and the phase 2 entry equality in
    `tests/test_stream_control.py` uses its own registry fixture (spec caller
    table).
  - `conftest._self_check` covers the new doubles.
- **Risks:**
  - Publishing two more services by default could change a phase 2 registry
    equality. Mitigation: grep `entries()` assertions. If one breaks, the
    phase 2 test's profile sets `services: [poll]` explicitly. That test is
    not a target, so prefer a fixture default that keeps the phase 2 shape
    wherever a test constructs the fixture without settings. The step
    records which shape was chosen.
  - `system:anonymous` must be accepted by `ChatEntry`. It is: any non-empty
    text.

### P5: Brain persona and ordered routes (R1; AC1, AC2-validator, AC3, AC4)
- **Files:** [modules/brain/__init__.py, modules/brain/module.yaml, tests/test_brain.py]
- **Description:**
  - **Manifest.** Declare `persona` (string, maxLength 2000) and `routes`
    (array, maxItems 16), with each route holding:
    - `name` (required, unique);
    - `match.kinds` (required, non-empty, drawn from `EVENT_KINDS`);
    - `match.command` (optional, one token without whitespace, at most 32
      characters);
    - `match.audience` (optional, one of `broadcaster`, `moderators`,
      `vips`, `subscribers`, `everyone`);
    - `instructions` (optional, at most 2000 characters);
    - `delivery` (optional, the existing delivery-list shape).
  - **`validate_settings` names the field for:** a duplicate name, an
    oversized persona, instructions or command, a 17th route, and an unknown
    kind.
  - **`_system_prompt(route)`.**
    - With neither key set it returns the phase 2 constant **unchanged**
      (the same object/text).
    - A `persona` replaces only the opening identity line.
    - The fixed tool-usage and plain-text lines are always kept.
    - The matched route's `instructions` are appended as their own paragraph.
  - **Route match.** Evaluated once per run from the triggering event:
    - The kind must be in `kinds`.
    - `command`, when set, must equal the first whitespace-separated token
      of the text, compared case-insensitively as a whole token (`!brb` ≠
      `!brbx`; `!ask !brb` does not match).
    - `audience`, when set, is satisfied only by trusted role claims with
      provenance, reusing the trigger engine's audience semantics (not the
      text).
    - The first match wins. With no match the phase 2 path is unchanged.
  - **Route delivery.**
    - `delivery` replaces `_delivery_for(platform, channel)` for that run.
    - Route lists are resolved at `prepare` through the existing
      `_resolve_delivery`, with the same diagnostics.
    - **Startup safety check:** a route whose list holds any write other than
      `chat.write` and `audio.speak` must declare `audience` `broadcaster` or
      `moderators`, or have `kinds ⊆ PLATFORM_NOTICE_KINDS`. Otherwise
      `prepare` fails naming the route.
  - **Allowlisted test.** Rewrite
    `test_manifest_declares_v2_shape_settings_hook_and_no_grant` so its
    settings set is the phase 2 set plus `persona` and `routes`.
- **Dependencies:** [P2, P4]
- **Tests:**
  - AC1: without `persona`/`routes`, the system message of a run is
    byte-equal to a phase 2 golden captured from `_system_prompt()` before
    the change (stored as a literal in the test), on twitch and on the fake
    platform.
  - AC2: a 30-character persona is line 1, and both fixed lines are present.
    `validate_settings` rejects a persona of 2001 characters, 17 routes, a
    duplicate `name` and a 33-character `command`, each naming the field.
    The `--check-config` exit 2 of the same cases runs in P22.
  - AC3: routes A/B from the spec on the fake platform:
    - a raid notice delivers through exactly 2 entries (1 `chat.write`, 1
      `audio.play` through a scripted action binding);
    - `!missed recap` has X in the system message and 1 `chat.write` from
      the destination's list;
    - a plain mention matches no route and X is absent.
  - AC4:
    - an unsafe `!brb` route fails `prepare` naming it;
    - with `audience: broadcaster`, a trusted broadcaster `!brb` gives 1
      scene call (scripted `stream.scene.set` binding);
    - a text-claimed broadcaster gives 0;
    - `!ask !brb` matches no route.
- **Risks:**
  - Byte-identity (AC1) breaks if the prompt is rebuilt by joining parts.
    Mitigation: with no persona or route, return the untouched constant.
  - Route delivery lists interact with phase 2 delivery accounting
    (`_invoked_count`, `_confirmed_sends`). Mitigation: reuse the same
    `_DeliveryEntry` tuples, so the accounting code is unchanged.

### P6: Brain run principal `brain.watch`, model-proposable write offers and the generic run limit (R5, R6; AC25-brain, AC32-brain)
- **Files:** [modules/brain/__init__.py, tests/test_brain.py]
- **Description:**
  - **Principal.** Replace the constant principal at every call site
    (`_offered_tools`, `_delivery_call`, the model-proposed call path and
    `authorized(principal=...)` for deliveries) with the run's principal from
    decision 4. `PRINCIPAL` stays as the default name `brain`.
  - **Offers.** `_offered_tools` keeps offering authorized reads. It also
    offers a write iff `spec.model_proposable` and the authorized view for
    (run principal, destination) grants it.
  - **Model write calls.** A model call to a write goes through the executor
    as a read call does. A second call of the same model-proposable action in
    one run returns `refused`/`run_limit` to the model with 0 executor
    invocations. A call to a write that is not proposable keeps the phase 2
    `not_a_read_action` error.
  - **Scope.** No module or action name is added.
- **Dependencies:** [P5]
- **Tests:**
  - AC25 brain half, with a test-local proposable write registered on the
    executor: it is offered only with a granting rule, and a granted
    `chat.write` is offered 0 times over a run.
  - A second proposable call in one run is `refused run_limit`, and the
    provider sees 1 call.
  - AC32 brain half: a run triggered by a `watch_tick`-kind event uses
    principal `brain.watch`:
    - a rule granting `screen.capture` to `brain` only gives 0 capture offers
      and 0 calls in that run, while a chat run of the same channel is
      offered it;
    - a rule naming `brain.watch` gives 1 provider invocation.
  - Phase 2 authorization tests stay green: every chat run's principal is
    still `brain`.
- **Risks:** a call site missed in the swap would silently authorize a watch
  run as `brain`. Mitigation: grep `PRINCIPAL` after the change. Only the
  default definition and the derivation function may reference it. The test
  asserts the principal on every recorded `ActionCall` of a watch run.

### P7: Twitch community notices, opt-in subscriptions and the `:` identity refusal (R1, R6; AC5, AC32-twitch)
- **Files:** [modules/twitch/__init__.py, modules/twitch/module.yaml, tests/test_twitch.py, tests/test_examples.py]
- **Description:**
  - **Manifest.** Declare `notices.kinds` (array of `sub`, `resub`,
    `sub_gift`, `community_sub_gift`, `raid`, `follow`, default empty) and the
    `event_kind` trigger type. The default policy, action and credentials are
    unchanged.
  - **Subscriptions.** Activation creates `channel.chat.message` as today.
    It adds `channel.chat.notification` iff one of the chat-notification
    kinds is listed, and `channel.follow` (version 2, needs the moderator
    follower-read scope) iff `follow` is listed.
  - **Follow degradation.** A rejected follow subscription (non-2xx) emits
    one `module.degraded` naming the `follow` capability and reason. Chat
    continues.
  - **Mapping.** `_normalize_notification` maps `notice_type` `sub`,
    `resub`, `sub_gift`, `community_sub_gift` and `raid` (raid author = the
    raider from the event's raid block) and `channel.follow` → `follow`,
    each only when its kind is listed.
    - A notice carries its `kind`, its author as viewer identity, the
      `system_message` text and trusted provenance.
    - Other notice types (`announcement`, …) and unlisted kinds are ignored
      and counted by a new `notices_ignored` counter.
    - An anonymous gift is fed as `system:anonymous` and never admitted
      (decision 3).
  - **`:` refusal.** Any author id containing `:` is refused and counted in
    the invalid counter before publication and admission.
  - **Allowlisted tests.** Rewrite `test_manifest_declares_twitch_source_and_sink`
    (settings + `notices`, types + `event_kind`). In
    `test_manifests_are_unique_and_have_coherent_capabilities`, update only
    the twitch-key expectation (decision 11).
- **Dependencies:** [P4]
- **Tests:** (AC5 in full, on the existing scripted EventSub transport)
  - Without `notices` there is exactly 1 subscription; with `[sub, raid,
    follow]` there are 3.
  - A raid from 42 gives one `raid` event with author `42`, 1 chat-context
    entry and 1 admitted run in session `(twitch, c, 42)` under
    `event_kind [raid]`.
  - `announcement` gives 0 events and `notices_ignored` + 1.
  - An anonymous community gift gives 1 context entry and 0 admissions.
  - A rejected follow subscription gives 1 `module.degraded` naming
    `follow`, and a subsequent chat message is admitted.
  - AC32 twitch half: author id `x:y` gives 0 admissions and invalid + 1.
- **Risks:**
  - Real EventSub payload field names (`chatter_user_id`, `notice_type`,
    `raid.user_id`, `community_sub_gift.id`, `chatter_is_anonymous`) must
    match the public schema. The step pins them in fixtures copied from the
    EventSub reference, and the P24 trial checks them for real.
  - The follow subscription may need a different condition
    (`moderator_user_id`). Build it from the configured bot identity.

### P8: Twitch publishes the `clip` and `moderation` services (R2, R5, R7-twitch; AC12-twitch service, AC28 platform half)
- **Files:** [modules/twitch/__init__.py, tests/test_twitch.py, tests/conftest.py]
- **Description:** Add two service classes in `modules/twitch`, both published
at activation only when `context.services.available`, next to `poll`.
  - **Clip service.** `create(broadcaster)` → `POST helix/clips`, one
    request, never retried. `lookup(clip_id)` → `GET helix/clips?id=`.
    Outcomes are classified into the taxonomy:
    - 202 → `accepted(id, edit_url)`;
    - a 404 or "not live" answer → `offline`;
    - 401/403/400 → `rejected`;
    - 429 → `rate`;
    - 5xx or a lost response → `uncertain`.
  - **Moderation service.**
    - `operations = {delete_message, timeout}`.
    - `delete_message` → `DELETE helix/moderation/chat?message_id=`.
    - `timeout` → `POST helix/moderation/bans` with `duration`. The unit is
      seconds and there is no rounding. A missing or zero duration is
      refused locally, so the ban endpoint is never called without a
      duration: no permanent ban.
    - The outcomes are `ok`, `rejected`, `rate` and `uncertain`.
  - **Credentials.** Both reuse the module's existing credential handling
    and redaction.
  - **`conftest.py`** gains HTTP answer builders for these endpoints on the
    existing scripted transport.
- **Dependencies:** [P7]
- **Tests:**
  - Activation on a context with services publishes `(clip, twitch)` and
    `(moderation, twitch)`. On a default context it publishes nothing and
    succeeds.
  - Each HTTP status maps to its outcome, with 1 request each.
  - A timeout without a duration sends 0 requests.
  - No credential value appears in any trace (a `trace_texts` scan).
- **Risks:** Twitch's clip creation needs scope `clips:edit`, and moderation
  needs `moderator:manage:chat_messages` and
  `moderator:manage:banned_users`. A token missing a scope surfaces only at
  call time as `rejected`, which is the honest taxonomy. The README (P25)
  lists the scopes.

### P9: `modules/clips` — `stream.clip.create` (R2; AC8, AC9, AC10, AC11, AC12-required)
- **Files:** [modules/clips/__init__.py, modules/clips/module.yaml, pyproject.toml, tests/test_clips.py, tests/test_examples.py, tests/test_profiles.py]
- **Description:**
  - **Manifest v2.** Declares `stream.clip.create`: version 1, write,
    permission `stream.clip`, destinations `*/*/clip`, delivery
    `text_argument: none`, timeout 20, idempotency `none`, argument schema
    `{}`, no `model_proposable`.
  - **Settings and validator.** `min_interval_seconds` (5–3600, default
    30), `max_waiters` (1–64, default 4), `required` (a strict bool, so the
    validator rejects `"yes"`). No grant.
  - **`prepare`.** Resolves `clip` services per enabled platform through
    `services.entries()`. A platform without one is unbound with reason
    `platform_unsupported`. If no platform has one: `required: true` → fail
    naming `clips` and `platform_unsupported`; otherwise every platform is
    unbound and startup continues.
  - **Call path, per channel:**
    1. Admission: waiter count > `max_waiters` → `refused resource_busy`.
    2. Take the per-channel lock.
    3. Cooldown against the last create-request instant →
       `refused cooldown` with 0 requests.
    4. `mark_emitted`, stamp the cooldown, and send one create.
    5. Classify per the R2 taxonomy.
    6. On acceptance, confirm by lookups per decision 8 until
       `acceptance + 15 s`:
       - found → `success {clip_id, url, confirmed_at}`;
       - window elapsed → `error clip_not_created`;
       - the lost-response and server-error paths →
         `external_unknown cause confirmation_lost`.
  - **Traces.** The `action.completed` trace carries the status, the code
    and `clip_id`.
  - **Packaging.** Add the `modules.clips` pyproject line. Update the
    allowlisted catalog assertions to the running values (decision 11).
- **Dependencies:** [P3, P4, P8]
- **Tests:** (`tests/test_clips.py`, fake platform with `ScriptedClipService`
  and `ManualClock`)
  - AC8: a success after exactly 1 create; with no rule, `refused` and 0
    requests.
  - AC9: a call at 29 s is `refused cooldown` with 0 requests; a call at
    30 s sends 1.
  - AC10: every row of the spec's table, each asserting ≤ 1 create:
    - the call deadline at 12 s ends `external_unknown`;
    - a deadline before sending ends `timeout` with 0 requests.
  - AC11: 5 concurrent calls give 1 send and 4 `refused cooldown`; a sixth
    concurrent call gives `resource_busy`; there is exactly 1 create in
    total, and the overlap flag is never set.
  - AC12 without Kick: the twitch binding is ready for `twitch/*/clip`, and
    the success trace has the `clip_id` and 0 credential values. With the
    fake platform set to `services: [poll]` as the only platform:
    `required: true` fails naming `clips` and `platform_unsupported`, unset
    leaves it unbound and startup completes, and `"yes"` is rejected. The
    Kick-named half of AC12 runs in P19.
- **Risks:**
  - Waiters released after the first call must re-check the cooldown
    **after** acquiring the lock, or AC11 would send a second request.
  - A lookup loop driven by the injected sleeper must not hold the lock past
    the deadline, or cancellation would leak waiters.
  - The drain must end in-flight calls in a `finally`. Per the coordinator
    note, a drain using its whole budget is reported timed out: assert the
    outcome, not status 0.

### P10: `modules/viewer_memory` — the file store, bounds, retention, eviction, prepare scan, sweep and `!forgetme` (R3; AC13–AC18, AC19-chat)
- **Files:** [modules/viewer_memory/__init__.py, modules/viewer_memory/module.yaml, pyproject.toml, tests/test_viewer_memory.py, tests/test_examples.py, tests/test_profiles.py]
- **Description:**
  - **Manifest.**
    - Settings: `directory` (required), `retention_days` (1–365, 30),
      `max_files` (1–100000, 1000), `max_file_bytes` (512–65536, 4096),
      `max_total_bytes` (≥ `max_file_bytes`, ≤ 1073741824, 16777216),
      `max_notes` (1–100, 10), `sweep_interval_seconds` (60–86400, 3600),
      `forget_command` (`!forgetme`, empty disables it).
    - The validator checks the cross-field bound.
    - The module consumes `channel.chat.message`. Actions are declared in
      P11. No grant.
  - **File name.** `sha256(json.dumps([platform, channel_id, viewer_id],
    ensure_ascii=False, separators=(",", ":"))).hexdigest() + ".json"`. A JSON
    array is collision-free and no identifier appears in the name.
  - **Content.**
    - `format: 1`, the key fields, `display_name` (cut to 64 characters),
      `first_seen`, `last_seen`, `last_used` (UTC ISO-8601), `use_count`,
      `interactions` and `notes`.
    - Each note has `at`, `viewer_text` ≤ 200, optional `reply_text` ≤ 200
      and `delivery`.
    - Oldest notes are dropped until the file holds ≤ `max_notes` notes and
      ≤ `max_file_bytes` bytes.
  - **Atomic write.** Write a temporary file in the same directory, `fsync`,
    then `os.replace`.
  - **Eviction before a write.** If the count or total would exceed a bound,
    the candidates are every memory file except the target. They are sorted
    by (`last_used`, `use_count`, `first_seen`, name) and deleted in order
    until both bounds hold after the write. One `memory.removed {reason:
    evicted, count}` fact is published per eviction pass.
  - **Retention.** `last_seen` older than `retention_days` (injected clock)
    → deleted at prepare and at each sweep (`expired`), and never returned.
  - **Prepare scan.**
    - Only names matching `^[0-9a-f]{64}\.json$` are read.
    - Malformed or oversized files are deleted (`corrupt`).
    - Expired files are deleted.
    - Eviction runs if the bounds still fail, all before readiness.
    - The sweep runs every `sweep_interval_seconds` as a supervised task.
  - **`!forgetme`.** For a `kind == message` event whose text is exactly
    `forget_command` from a trusted author, delete that author's file for
    the platform and channel (`erased`). This happens whatever the trigger
    decides, because the module consumes the event itself. Facts carry the
    reason and count only.
  - **In-memory index.** An index of metadata keeps eviction ordering
    O(n log n) without re-reading files. It is rebuilt at prepare.
  - **Packaging.** Add the `modules.viewer_memory` pyproject line. Update
    the running catalog counts.
- **Dependencies:** [P4]
- **Tests:** (the store API is driven directly; the module goes through a
  `runtime_context`)
  - AC13: the name regex matches, the name equals the key's SHA-256 digest
    plus `.json` and contains neither `v1` nor a non-hex channel id
    (`chan-1`), the JSON fields are present, and `(kick, c1)` gives a
    distinct file (wording aligned with the P27F3 AC13 clarification).
  - AC14: C is deleted, then the `first_seen` tie-break, then the name
    tie-break. The deletion publishes 1 fact with count 1.
  - AC15: the total-bytes bound holds, and the target is never deleted.
  - AC16: at 30 d + 1 s the record is not returned and is deleted at the
    sweep; at 29 d it is returned; the default is 30.
  - AC17: the 11th note drops the oldest. Over 50 writes of 200-character
    notes with 1024 bytes, every file size is ≤ 1024.
  - AC18: a malformed file and an oversized file are deleted (count 2,
    `corrupt`) and `notes.txt` is untouched; 5 valid files with
    `max_files: 3` leave 3 before `module.ready`.
  - AC19 chat half: `!forgetme` from trusted `v1` gives 1 `erased` fact with
    no id, even with a rejecting policy. `!forgetme please` and a notice
    whose text is `!forgetme` delete nothing.
  - A crash between the temporary write and the replace leaves the old file
    intact (fault-injected `os.replace`).
- **Risks:**
  - A single note larger than the file budget: `viewer_text`/`reply_text`
    are ≤ 200 characters and `max_file_bytes` ≥ 512, so metadata plus one
    note can still exceed 512 bytes with 64 four-byte characters. The rule
    is "drop oldest notes first, and if a lone newest note still does not
    fit, store 0 notes". This keeps the file bound absolute (AC17), and it
    is recorded in the README.
  - Clock skew makes ISO timestamps compare lexicographically only in UTC
    with a fixed format. Pin the format.

### P11: `viewer_memory` actions `memory.recall` and `memory.record` (R4; AC16-recall, AC22-module)
- **Files:** [modules/viewer_memory/__init__.py, modules/viewer_memory/module.yaml, tests/test_viewer_memory.py, tests/test_examples.py]
- **Description:**
  - **Manifest.** Add:
    - `memory.recall`: read, permission `memory.recall`, destinations
      `*/*/memory`, args `{}` with `additionalProperties: false`;
    - `memory.record`: write, permission `memory.record`, destinations
      `*/*/memory`, no delivery, no `model_proposable`, timeout 5;
    - the `max_recall_bytes` setting (512–8192, 1024).
  - **`memory.record` arguments.** Unlike `memory.recall` (empty schema),
    its schema accepts the exchange data (decision 6): `viewer_text`
    (≤ 200, required), `reply_text` (≤ 200, optional), `delivery` (enum
    `confirmed`, `unconfirmed`, `none`, required) and optional
    `display_name`, with `additionalProperties: false`. It forbids every
    viewer-identity argument: there is no viewer id, platform or channel
    property, so those are refused as invalid.
  - **Viewer.** The viewer comes from `conversation_id` (decision 6).
    `system:`-namespaced or unparsable → `error no_viewer`.
  - **Recall result.**
    - An unknown or expired viewer → `{known: false}`.
    - Otherwise `known`, `display_name`, `first_seen`, `last_seen`,
      `interactions`, then notes newest first, fitted to `max_recall_bytes`
      (measured as the serialized UTF-8 observation).
    - Oldest notes are dropped first. If the metadata alone is over the
      limit, `display_name` is shortened by whole characters from its end.
      `truncated: true` is set when anything was left out.
    - A recall that returns a record is a **use** (`use_count` + 1,
      `last_used`).
  - **Record.** Creates or updates the file (`interactions` + 1,
    `last_seen`, `display_name`, a use), with eviction and the note bounds
    from P10.
- **Dependencies:** [P10]
- **Tests:** (through the executor, so argument validation precedes the
  provider)
  - AC22 module half:
    - an argument `viewer_id` is refused as invalid with 0 provider
      invocations, on `memory.record` (alongside valid exchange data) and on
      `memory.recall` (whose schema is `{}`);
    - `memory.record` with only `viewer_text` and `delivery` succeeds, and
      with `reply_text` and `display_name` added also succeeds;
    - 12 notes of 200 characters at 1024 bytes give ≤ 1024 bytes, newest
      first, `truncated`;
    - 64 four-byte characters + 10 notes at 512 give ≤ 512 bytes, all
      metadata keys, and a `display_name` that is a prefix of the stored
      one;
    - an 80-character name is stored as 64;
    - 511 is rejected by the validator;
    - a `system:watch` conversation gives `error no_viewer`.
  - AC16 recall half: at 30 d + 1 s the result is `known: false`.
  - A recall increments `use_count`.
- **Risks:**
  - The observation budget measure must match the executor's
    `observation_size` or the brain's budget accounting would disagree.
    Mitigation: fit on the exact serialization the provider returns as its
    text part.
  - The `conversation_id` format is an assumption to verify first
    (`SessionKey.serialize()` and its inverse in `core/contracts.py`). If no
    inverse exists, parse with the same separator rule, without editing
    `core/`.

### P12: `python -m modules.viewer_memory` offline command (R3; AC19-CLI)
- **Files:** [modules/viewer_memory/__main__.py, tests/test_viewer_memory.py]
- **Description:**
  - **Reading.** `argparse` subcommands `forget --config <file> --platform
    <p> --channel <c> --viewer <v>`, `forget-all --config <file>` and
    `stats --config <file>`. The command reads the YAML configuration with
    PyYAML, takes `modules.viewer_memory.directory` (the same settings path
    the runtime uses), and runs the P10 validator on those settings.
  - **Actions.**
    - `forget` deletes the one hashed file.
    - `forget-all` deletes every memory-named file and keeps the rest.
    - `stats` prints `files=<n> bytes=<b>`.
  - **Exit codes.** 0 on success; 2 on an unreadable or invalid
    configuration.
  - **Isolation.** The command never imports `aiohttp`, never starts the
    runtime and resolves no `${…}` secret. It needs none.
- **Dependencies:** [P11]
- **Tests:** (AC19 CLI half, via `main(argv)` in-process with `socket.socket`
  monkeypatched to raise, plus one `subprocess` run for the `-m` entry)
  - `forget` removes exactly 1 file.
  - `forget-all` leaves 0 memory files and keeps `notes.txt`.
  - `stats` output matches.
  - An invalid configuration exits 2.
- **Risks:** the `${NAME}` references of the profile must not be required by
  the CLI. Mitigation: read only the memory module's settings subtree and
  refuse a `${…}` in `directory` with exit 2 and a message. Unresolved
  references elsewhere are ignored.

### P13: Brain post-delivery `memory.record` step (R4; AC20, AC21, AC22-offer, AC23)
- **Files:** [modules/brain/__init__.py, tests/test_brain.py]
- **Description:**
  - **When the step runs.** After `_deliver_all` has produced the run's
    delivery outcome, and only if:
    - the session viewer is a platform viewer (not `system:`);
    - `memory.record` is bound for `(platform, channel, memory)`;
    - the run principal's authorized view grants it.
  - **The call.** One call is issued, with:
    - `viewer_text` = the triggering text[:200];
    - the branches are evaluated in this order, and the first match wins:
      1. **absent delivery** — the run produced no delivery entry at all
         (no route matched, the model answered nothing deliverable, or the
         run ended before delivery): `delivery: none` and no reply. This
         branch is explicit and precedes the others, because "every entry
         is `success`" is vacuously true of an empty list;
      2. the list is non-empty and every required entry is `success`:
         `reply_text` = the delivered text[:200] and `delivery: confirmed`;
      3. any entry is `external_unknown`: `delivery: unconfirmed` and no
         reply;
      4. otherwise (a failed delivery: `error`, `timeout`, `refused`):
         `delivery: none` and no reply.
  - **Budget.** The call uses the run's remaining deadline and one action
    call of the run budget.
  - **Skip.** Skipped, with a `memory_record: skipped` field in the run
    record/trace plus the reason, when 0 action calls remain or the
    remaining deadline is < 5 s (read from the spec's `timeout_seconds`, not
    a literal).
  - **No effect on the run.** The result never alters the terminal status,
    the delivery summary or the correlation fields.
  - **Offers.** `memory.record` is never offered: it is not proposable, so
    P6 excludes it. The step adds an assertion-level guard.
- **Dependencies:** [P6, P11]
- **Tests:** (with the real `viewer_memory` module on the fake platform and a
  scripted model)
  - AC20: `known: false`, then `known: true` with `interactions: 1` and a
    note `reply_text: hello`, `confirmed`.
  - AC21: `external_unknown` gives `unconfirmed` with no reply; `error`
    gives `none` with no reply; an **absent delivery** (a scripted model
    turn that produces no deliverable text, so the run has 0 delivery
    entries) gives a note with `at`, `viewer_text`, `delivery: none` and no
    `reply_text` key — never `confirmed`. Each run's status and delivery outcome
    equals a twin run without `viewer_memory` (compared field by field).
  - AC22 offer half: 0 `memory.record` occurrences in any model request
    body of a run where it is granted.
  - AC23: 0 calls remaining gives skipped; 4.9 s remaining (ManualClock)
    gives skipped; 1 call and 5 s gives exactly 1 call.
- **Risks:**
  - The record call can itself time out at the run deadline. It must be
    awaited under the run's cancellation scope without converting a late
    failure into a run failure. Wrap it so any record outcome is traced
    only.
  - A delivery through a route (P5) must feed the same "delivered text".
    Use the text argument of the `chat.write`/`audio.speak` entry that
    succeeded.

### P14: `modules/moderation` — `moderation.request`, modes `alert`/`act`, strict rules, platform execution and decision facts (R5; AC24, AC25-offer via real module, AC27 except kick, AC28 core cases)
- **Files:** [modules/moderation/__init__.py, modules/moderation/module.yaml, pyproject.toml, tests/test_moderation.py, tests/test_examples.py, tests/test_profiles.py]
- **Description:**
  - **Manifest v2.** `moderation.request`: version 1, write,
    `model_proposable: true`, permission `moderation.request`, destinations
    `*/*/moderation`, no delivery, idempotency `none`.
    - Arguments: `operation` (enum `delete_message`, `timeout`),
      `message_id`, `reason` ≤ 200, `duration_seconds` (integer, only valid
      with `timeout`, required with it).
    - The module consumes `channel.chat.message`. No grant.
  - **Settings and validator.**
    - `mode` (`alert` default, `propose`, `act`) and
      `channels.<platform>/<channel_id>.mode` overrides;
    - `act.operations` (default `[delete_message]`);
    - `max_timeout_seconds` (1–3600, 300);
    - `max_actions_per_window` (1–20, 3), `window_seconds` (60–86400, 600);
    - `per_target_cooldown_seconds` (0–86400, 600);
    - the `propose.*` settings are declared here and used in P15.
  - **Prepare.** Resolves `moderation` services per platform and reads the
    chat context and the companion identity.
  - **Message index.** Built from consumed `kind == message` events
    (decision 7).
  - **Modes:**
    - `alert` → `success disposition alerted`, 0 platform requests;
    - `act` → apply now.
  - **Application.** The strict rules are checked in the spec's order, each
    refusing with its code and 0 requests. `duration_out_of_range` is
    checked after the platform service's rounding hook. An allowed
    application:
    1. `mark_emitted`;
    2. one service request;
    3. the result maps to `ok` → `success applied`, `rejected` →
       `error platform_rejected`, `rate` → `error rate_limited_platform`,
       lost/5xx/deadline → `external_unknown`;
    4. the window and target-cooldown counters count every sent request
       (see "Concurrency" below: they are reserved before the send).
  - **Concurrency (serialization and reservation).** Several runs (and, in
    P15, approval commands and `auto_apply`) can call the application
    concurrently, and the service request is an `await`. Without
    protection, two calls could both pass the `rate_limited` and
    per-target cooldown checks while the first request is pending. The
    module therefore holds one `asyncio.Lock` per `(platform, channel_id)`
    for the **check-and-reserve** section only:
    1. under the lock, evaluate every strict rule against the current
       counters;
    2. still under the lock, if allowed, reserve the slot: append the
       window timestamp and set the target-author cooldown instant;
    3. release the lock, then `mark_emitted` and send the one request.
    A reserved slot is always followed by a send (nothing between the
    reservation and the send can refuse), so "counters count every sent
    request" still holds and a reservation is never rolled back, whatever
    the platform result (the conservative choice: a lost or rejected
    request still consumed its slot). The request itself runs outside the
    lock, so a slow platform does not block unrelated channels or the
    checks of other targets beyond the reservation. P15's approval and
    `auto_apply` paths call this same function; there is no second
    application path.
  - **Facts.** Every request publishes exactly one `moderation.decision`
    fact (mode, disposition/status, code, operation, platform, channel,
    message id, target author id, reason ≤ 200). The audit module records
    it through its existing fact subscription. Verify that its pattern
    covers the new type, and document otherwise.
  - **Packaging.** Add the pyproject line and the running catalog counts.
- **Dependencies:** [P3, P6, P8]
- **Tests:**
  - AC24: the default mode alerts, with 0 service requests and 1 fact.
  - AC25 on the real module: with a scripted model, `moderation.request` is
    offered only with a rule, and a second request in a run is
    `refused run_limit`.
  - AC27 on twitch/fake: each rule's code with 0 requests:
    - the moderator, VIP, broadcaster and companion cases for
      `target_protected`;
    - the 4th application inside 600 s for `rate_limited`.
  - AC28: an allowed delete sends 1 request and ends `applied`; the
    rejected, rate and lost cases each send 0 second requests.
  - Concurrency: with a scripted service whose request blocks on an
    `asyncio.Event`, `max_actions_per_window: 3` and 5 distinct targets,
    5 `moderation.request` calls launched with `asyncio.gather` give
    exactly 3 service requests and 2 `rate_limited` refusals (checked
    before the event is set). Two concurrent calls on messages of the
    same author give 1 request and 1 per-target cooldown refusal. Two
    concurrent calls on different channels both send (the lock is per
    channel).
  - The fact count equals the request count over every case.
  - No test uses a permanent-ban path: a grep of the module for
    ban-without-duration is empty.
- **Risks:**
  - `target_protected` depends on roles seen in the indexed event. A
    message older than the index but still in the chat context is
    `target_unknown`, which is the conservative choice. The README states
    this.
  - The per-target cooldown key must be the target **author**, not the
    message.
  - Concurrent applications could overshoot `max_actions_per_window` or
    hit one target twice inside its cooldown if the counters were updated
    after the awaited request. Mitigation: the per-channel lock and
    reservation above. Risk of the mitigation: a lock held across an
    `await` would serialize platform latency; the lock covers only the
    synchronous check-and-reserve, never the request.

### P15: `moderation` propose mode, approval/rejection commands, expiry, `auto_apply` and per-channel overrides (R5; AC26, AC28-override, AC28-fact count)
- **Files:** [modules/moderation/__init__.py, modules/moderation/module.yaml, tests/test_moderation.py]
- **Description:**
  - **Proposal table.** Bounded by `max_pending` (1–256, 32), keyed by
    short ids from an injected id source. Each proposal has
    `proposal_ttl_seconds` (30–3600, 300). When the table is full, the
    proposal closest to expiry is evicted and publishes an `expired` fact.
  - **Proposing.** `propose` → `success disposition proposed, proposal_id`,
    0 requests. When the operation is in `propose.auto_apply` (default
    empty), the proposal is applied at once through P14's application path.
  - **Commands.** `approve_command` (default `!modok`) and `reject_command`
    (`!modno`) followed by `<id>`, from a `kind == message` event in the
    **same channel** with trusted `broadcaster` or `moderators` roles:
    - approval → the P14 application (the same function, so the same
      per-channel lock and reservation) → a fact (`applied` or the refusal
      or error);
    - rejection → a `rejected` fact;
    - an unknown id or an untrusted author → nothing (counted).
  - **Expiry.** A supervised expiry sweep on the injected clock publishes an
    `expired` fact.
  - **Overrides.** Per-channel `mode` overrides are resolved per request.
- **Dependencies:** [P14]
- **Tests:** AC26 in full:
  - a proposal id with 0 requests;
  - `!modok` from the trusted broadcaster gives 1 request and `applied`;
  - the same command from a viewer gives 0 requests;
  - `!modno` from a moderator discards the proposal;
  - expiry at 300 s gives 1 `expired` fact;
  - `max_pending: 2` makes the third proposal evict the one closest to
    expiry;
  - `auto_apply: [delete_message]` applies at once under the strict rules.
  - Concurrency: with the blocking scripted service and
    `max_actions_per_window: 1`, a `!modok` for proposal A and an
    `auto_apply` request launched together give exactly 1 service request
    and 1 `rate_limited` fact; two `!modok <id>` for the same proposal
    launched together give 1 request (the proposal is removed from the
    table under the channel lock before the application, so the second
    approval finds an unknown id).
  - AC28: the override `twitch/c2: act` leaves `twitch/c1` in `alert`.
  - Across every AC24–AC28 case in the file, `len(moderation.decision
    facts) == requests + approvals + rejections + expiries` (asserted by a
    shared fixture counter).
- **Risks:**
  - A command naming an id from another channel must not apply it: the
    channel check is part of the lookup key.
  - A double approval of one proposal could apply it twice. Mitigation:
    pop the proposal from the table in the same locked section that
    reserves the slot.
  - Approval-time strict rules use the state **at approval**, not at
    proposal. The message may have left the context, giving
    `target_unknown`.

### P16: `modules/watch` — sessions, cadence, caps, validator and `watch.state` facts (R6; AC29, AC30-validator, AC31 cadence/caps/max_active)
- **Files:** [modules/watch/__init__.py, modules/watch/module.yaml, pyproject.toml, tests/test_watch.py, tests/test_examples.py, tests/test_profiles.py]
- **Description:**
  - **Manifest v2.** Lifecycle role `input`. The module consumes
    `channel.chat.message` and declares the `event_kind` trigger type with
    default policy `event_kind: [watch_tick]`. No action, no grant.
  - **Settings.**
    - `channels` (1–4 entries of `platform/channel_id`);
    - `interval_seconds` (15–3600, 60);
    - `max_ticks_per_hour` (1–240, 30, and ≤ `3600 // interval_seconds`,
      a validator cross-check);
    - `max_active_seconds` (60–14400, 3600);
    - `activation` (`command` default | `startup`);
    - `start_command` (`!watch`), `stop_command` (`!unwatch`);
    - `command_audience` (`broadcaster` | `moderators`);
    - `prompt_text` (≤ 500).
  - **Session state per channel.**
    - Start on an exact `start_command` from a `kind == message` event with
      a trusted role of `command_audience`, or at `start_inputs` when
      `activation: startup`.
    - Stop on `stop_command` from that audience, at `max_active_seconds`,
      or at `stop_inputs`.
    - Each change publishes `watch.state {state, reason}`.
  - **Tick scheduler.** A supervised task on the injected clock emits a
    tick every `interval_seconds` while the session is active. A sliding
    one-hour deque enforces `max_ticks_per_hour`; a tick over the cap is
    skipped and counted.
  - **Hand-off.** In this step, ticks go to an internal emit hook. P17
    wires them to triggers and admission.
  - **Packaging.** Add the pyproject line and the running catalog counts.
- **Dependencies:** [P2, P4]
- **Tests:**
  - AC29:
    - unset activation gives 0 ticks over 600 s;
    - the broadcaster's `!watch` gives 10 ticks over 600 s at 60 s;
    - a viewer's `!watch` starts nothing;
    - `!unwatch` gives 0 further ticks;
    - `startup` ticks without a command.
  - AC30: `validate_settings` rejects `interval_seconds: 14`,
    `max_ticks_per_hour: 241`, `100` with 60 s, `max_active_seconds: 59` and
    5 channels, each naming the field. The `--check-config` exit 2 runs in
    P22.
  - AC31: with 15 s and cap 10, at most 10 ticks in any 3600 s window
    (checked by sliding over the tick stamps); `max_active_seconds: 600`
    gives no tick after t0 + 600 and `inactive max_active`.
- **Risks:** a tick scheduled exactly at the cap boundary is the classic
  off-by-one. Mitigation: define the window as `(t − 3600, t]` and test the
  boundary tick. Tests use `ManualClock` only, with no positive sleep.

### P17: `watch` ticks through triggers and admission, the in-flight rule, the `brain.watch` principal end to end, and shutdown (R6; AC31-in-flight, AC32, AC33)
- **Files:** [modules/watch/__init__.py, tests/test_watch.py]
- **Description:**
  - **The tick event.** A normalized schema-2 event: `kind: watch_tick`,
    text `prompt_text`, author `system:watch`, `message_id` from a
    monotonic per-channel counter, source `watch`.
  - **Evaluation and admission.** The tick is evaluated by the trigger
    engine with the module's policy. When accepted, it is admitted
    **directly** through the scheduler (`admit(session_key, work)`, as
    twitch does). It is never published as `channel.chat.message`, so it is
    never fed to the chat context or the users directory.
  - **In-flight rule.** The module tracks its admitted work per channel
    through the admission result/run-completion hook. A tick due while one
    is queued or running is skipped and counted. At most one watch run per
    channel is in flight.
  - **Shutdown.** `stop_inputs` cancels the tick task, publishes `inactive
    shutdown` and emits nothing after.
- **Dependencies:** [P6, P16]
- **Tests:**
  - AC31 in-flight: a held scripted model keeps the first watch run
    running; the next due tick gives skip + 1, and in-flight stays ≤ 1.
  - AC32 end to end with brain, the capture module's scripted source and a
    `watch_tick`:
    - 0 chat-context and users entries;
    - with a `brain`-only rule, 0 capture offers or calls;
    - with a `brain.watch` rule, 1 provider invocation on the same provider
      as a chat run.
  - AC33: shutdown during a session gives 0 ticks after `stop_inputs`, an
    `inactive shutdown` fact, and completion inside the global shutdown
    deadline on `ManualClock`.
- **Risks:**
  - The run-completion signal the scheduler exposes must be verified
    (`AdmissionResult`/`RunRecord`). If only admission-time results exist,
    track completion through the `brain.run.completed` trace keyed by
    session. The step chooses and documents the channel it uses.
  - The coordinator drain tie: assert outcomes, not status 0.

### P18: `modules/kick` — signed webhook ingestion, normalization, notices, badges, triggers and admission (R7, R1-notices, R6-identity; AC34, AC35-badges/mapping, AC32-kick)
- **Files:** [modules/kick/__init__.py, modules/kick/module.yaml, pyproject.toml, tests/test_kick.py, tests/conftest.py, tests/test_examples.py, tests/test_profiles.py]
- **Description:**
  - **Manifest v2.** Role `input`.
    - Trigger types `probability`, `audience`, `keyword` and `event_kind`,
      with default policy "mentions `companion_name`".
    - `chat.write` for `kick/*/chat`, declared in P19.
    - Credentials `client_secret` and `access_token`.
    - Settings: `listener.host`/`listener.port`, `public_key` (PEM, optional;
      otherwise fetched once at prepare through the injected transport),
      `channels`, `companion_name`, `notices.kinds`, dedup bounds. Plus the
      validator.
  - **Listener.** An `aiohttp.web` listener on the configured host and port.
  - **Accepting a delivery.** A delivery is accepted only if all of these
    hold, checked in this order:
    1. body ≤ 65536 bytes (read with a bounded reader);
    2. `Kick-Event-Signature` verifies (decision 9) over `message id + "." +
       timestamp + "." + raw body`;
    3. the timestamp is within 300 s of the injected clock;
    4. the message id is not in the bounded dedup window.
  - **Refusals** answer 4xx and count a per-reason counter, never
    publishing.
  - **Mapping.** `chat.message.sent` → `message`, `channel.followed` →
    `follow`, `channel.subscription.new` → `sub`,
    `channel.subscription.renewal` → `resub`, `channel.subscription.gifts` →
    `sub_gift`.
  - **Roles.** Badges `broadcaster`, `moderator`, `vip` and `subscriber`
    become trusted roles.
  - **Pipeline.** Anti-echo on the companion identity; the `:` refusal; the
    chat-context feed; trigger evaluation; admission, as twitch does.
  - **`conftest.py`** gains `KICK_TEST_KEY` (a fixed RSA test key pair: PEM
    public key plus integer private exponent, labelled test-only) and
    `SignedWebhookSender(key, clock)`, which signs with `pow(m, d, n)` and
    posts through an in-process `aiohttp` test client.
  - **Packaging.** Add the pyproject line and the running catalog counts.
- **Dependencies:** [P4, P7]
- **Tests:**
  - AC34: a valid delivery is published once as a `kick` event and
    admitted per policy. A bad signature, a 301 s-old timestamp, a
    65537-byte body and a duplicate id are each refused, counted and yield 0
    events. The listener binds the configured host/port (loopback, port 0
    resolved).
  - AC35 partial: the badge → audience checks and the four event-kind
    mappings.
  - AC32 kick half: an author with `:` gives 0 admissions and invalid + 1.
  - The verifier rejects a signature ≥ n and a wrong DigestInfo.
- **Risks:**
  - Kick's exact signature input and header names must match the public
    webhook documentation. The tests pin the documented shape, and the P24
    trial validates it against the real platform.
  - A committed RSA private key could trip a secret scan. Mitigation: name
    it test-only and keep it inside `tests/`. The hygiene secret check
    targets settings shapes in the README, not tests.
  - Webhook reachability (public HTTPS) is a deployment prerequisite,
    documented in P25 and not provided.

### P19: Kick `chat.write`, rate limits, `timeout`-only moderation service and capability view (R7, R2/R5 capability halves; AC35-rest, AC12-kick, AC27-kick, AC39-kick)
- **Files:** [modules/kick/__init__.py, modules/kick/module.yaml, tests/test_kick.py, tests/test_clips.py, tests/test_moderation.py, tests/test_examples.py]
- **Description:**
  - **`chat.write`.** The manifest declares `chat.write` for `kick/*/chat`
    with delivery text mapping as twitch does. A send is one POST to the
    platform chat endpoint:
    - more than 500 characters → `error text_too_long`, 0 requests;
    - 429 → `error rate_limited`, and further sends are blocked until the
      announced retry instant (`refused rate_limited`, 0 requests);
    - lost/5xx → `external_unknown`, never retried.
  - **Moderation service.** Published only when services are available,
    with `operations = {timeout}`. Durations are rounded **up** to whole
    minutes, and a result outside 1–10080 minutes is refused locally with 0
    requests. The request is one POST to the bans endpoint with a
    `duration`. It never omits the duration.
  - **No clip or poll service.**
  - **Catalog assertion (decision 11).** Declaring `chat.write` in the kick
    manifest makes it a second declarer. In `tests/test_examples.py`,
    `test_manifests_are_unique_and_have_coherent_capabilities` is rewritten
    in this step, and only in its `chat.write` multiplicity assertion, to
    the running value: `chat.write` is declared by exactly twitch and kick.
    P21 moves it to twitch, kick and youtube; P23 pins that final value.
    The manifest count (set by P18) and the action-name count (unchanged:
    `chat.write` is not a new name) are not touched.
- **Dependencies:** [P9, P14, P18]
- **Tests:**
  - AC35 rest: 500 characters give 1 request; 501 give `text_too_long` and
    0; a 429 with retry 30 s gives `rate_limited`, and a send 29 s later is
    `refused rate_limited` with 0 requests; 90 s is sent as 2 minutes;
    10081 minutes gives 0 requests.
  - Catalog: the rewritten `chat.write` multiplicity assertion passes with
    exactly {twitch, kick}, and the full suite is green at the end of the
    step (decision 11).
  - AC12 kick half (in `tests/test_clips.py`): with twitch + kick,
    `stream.clip.create` is ready for twitch and unbound for kick with
    `platform_unsupported`. With only kick: `required: true` fails naming
    `clips`/`platform_unsupported`; unset starts with the action unbound.
  - AC27 kick half (in `tests/test_moderation.py`): `delete_message` on
    kick in `act` gives `platform_unsupported` and 0 requests.
- **Risks:** the Kick rate-limit header shape (a `Retry-After` in seconds or
  a reset timestamp) must be read defensively. An unparsable answer blocks
  for a bounded default and is traced.

### P20: `modules/youtube` — OAuth refresh, live-chat polling cadence and the daily quota ledger (R7; AC36, AC37)
- **Files:** [modules/youtube/__init__.py, modules/youtube/module.yaml, pyproject.toml, tests/test_youtube.py, tests/conftest.py, tests/test_examples.py, tests/test_profiles.py]
- **Description:**
  - **Manifest v2.** Role `input`.
    - Trigger types as for kick.
    - Credentials `client_secret` and `refresh_token`, plus a non-secret
      `client_id` setting.
    - Settings: `channels`, `companion_name`, `min_poll_interval_seconds`
      (1–60, 5), `quota.daily_units`, `quota.write_reserve_units`,
      `quota.costs` (`list`, `insert`, `delete`, `ban`, `broadcast_lookup`),
      `notices.kinds`. Plus the validator.
  - **Token manager.** The refresh token is exchanged at the token
    endpoint. The access token is refreshed at `expires_at − refresh_margin`
    (a fixed 60 s constant, documented), and only when a request needs it.
    A refused refresh publishes one `module.degraded` naming
    `auth_refresh_failed`, and polling stops.
  - **Poller.** Resolves the channel's active broadcast `liveChatId`, then
    lists at `max(pollingIntervalMillis/1000, min_poll_interval_seconds)` on
    the injected clock.
  - **Quota ledger.**
    - Every request is charged its configured cost.
    - A read is issued only if `remaining − cost ≥ write_reserve_units`.
      Otherwise the module reports `quota_exhausted` (degraded, once per
      day) and stops reading.
    - A send is issued only if `remaining ≥ cost`. Otherwise it is
      `refused quota_exhausted` with 0 requests.
    - The ledger resets at 00:00 America/Los_Angeles (decision 10), and
      reads resume.
  - **`conftest.py`** gains `ScriptedTokenEndpoint` (issued tokens,
    refusals, a request log) and `ScriptedLiveChatAPI` (broadcast lookup,
    list pages with `pollingIntervalMillis`, insert/delete/ban answers, a
    per-endpoint request count).
  - **Packaging.** Add the pyproject line and the running catalog counts.
- **Dependencies:** [P4, P7]
- **Tests:**
  - AC36:
    - a 3600 s token is refreshed once before expiry and not earlier;
    - a refused refresh gives 1 `module.degraded auth_refresh_failed`;
    - a 2000 ms server interval with min 5 gives ≤ 12 lists per 60 s;
    - an 8000 ms interval gives 8 s spacing.
  - AC37: 100 units, list cost 5, send cost 50, reserve 50 → exactly 10
    lists, then `quota_exhausted` and 0 reads. The send check is exercised
    in P21 once `chat.write` exists. After the LA midnight, reads resume.
    The LA-midnight function is tested at both DST transitions and on an
    ordinary day.
- **Risks:**
  - The AC37 arithmetic: 10 lists × 5 = 50 leaves 50 = reserve, so the
    11th read would dip into the reserve and must not issue. The boundary is
    `remaining − cost ≥ reserve`.
  - The broadcast lookup is also charged. Mitigation: the AC37 test sets a
    lookup cost of 0 or accounts for it. The test states which it uses.

### P21: YouTube ingestion mapping, `chat.write`, moderation service and degradation (R7, R1-notices, R6-identity; AC37-send, AC38, AC32-youtube, AC39)
- **Files:** [modules/youtube/__init__.py, modules/youtube/module.yaml, tests/test_youtube.py, tests/test_examples.py]
- **Description:**
  - **Normalization.** Listed messages become schema-2 events for platform
    `youtube`.
  - **Roles.** From `authorDetails`: `isChatOwner` → broadcaster,
    `isChatModerator` → moderators, `isChatSponsor` → subscribers.
  - **Notice mapping.** `newSponsorEvent` → `sub`,
    `memberMilestoneChatEvent` → `resub`, `membershipGiftingEvent` →
    `sub_gift`, `superChatEvent`/`superStickerEvent` → `tip`, when listed.
    Other types are ignored and counted.
  - **Pipeline.** Anti-echo, the `:` refusal, dedup, the chat-context feed,
    triggers and admission.
  - **`chat.write`.** Declared for `youtube/*/chat`:
    - more than 200 characters → `text_too_long`, 0 requests;
    - the quota check (P20);
    - a 403 `rateLimitExceeded`/429 → `rate_limited` with a block until the
      retry;
    - uncertain → `external_unknown`.
  - **Moderation service.** `{delete_message, timeout}`:
    - delete → `liveChatMessages.delete`;
    - timeout → `liveChatBans.insert` with `type: temporary` and
      `banDurationSeconds`;
    - a `permanent` type is never built.
  - **No clip or poll service.**
  - **Catalog assertion (decision 11).** The youtube `chat.write`
    declaration makes the running multiplicity assertion in
    `tests/test_examples.py` (set to {twitch, kick} by P19) exactly
    {twitch, kick, youtube}; only that assertion changes.
- **Dependencies:** [P20]
- **Tests:**
  - AC37 send half: after exhaustion one send succeeds with 1 insert; a
    second is `refused quota_exhausted` with 0 requests.
  - AC38: 200 characters give 1 request; 201 give `text_too_long` and 0;
    the three role mappings; the four notice mappings; delete and timeout
    make 1 request each.
  - AC32 youtube half: an author with `:` gives 0 admissions and
    invalid + 1.
  - AC39, with twitch + kick + youtube enabled on one runtime:
    - `chat.write` has exactly 3 bindings on disjoint destinations;
    - the same viewer id on two platforms gives two sessions;
    - `stream.poll.create` and `stream.clip.create` are unbound for kick and
      youtube with `platform_unsupported`;
    - a case-insensitive word scan of `core/` finds 0 `kick`/`youtube`.
- **Risks:**
  - YouTube returns 403 for several unrelated reasons. Classification by
    `error.errors[0].reason` must separate rate (`rateLimitExceeded`) from
    rejected (`forbidden`, `insufficientPermissions`) and from quota
    (`quotaExceeded`, mapped to `quota_exhausted` and syncing the local
    ledger to 0).
  - `stream.poll.create`'s unbound reason for kick/youtube comes from
    `stream_control`. If its current reason string differs from
    `platform_unsupported`, a change there would be outside the targets.
    Verify first. If it differs, record it as a spec gap for the P26 gate
    instead of editing `modules/stream_control/`.

### P22: `presence.yaml.example`, the presence-pack scenario and `--check-config` coverage (R1, R8; AC2-check-config, AC6, AC30-check-config)
- **Files:** [presence.yaml.example, tests/test_presence_pack.py, tests/test_profiles.py]
- **Description:**
  - **Enabled modules.** twitch (with `notices.kinds`), chat_context,
    users, audio_output, stream_control, clips, viewer_memory, moderation
    (`mode: alert`), watch (`activation: command`) and brain.
  - **Brain.** A `persona` and the routes from the spec's list, each with
    its safe audience or notice-only kinds:
    - mention replies;
    - `!missed` summary;
    - translation;
    - thanks on notices with a jingle (`audio.play`);
    - broadcaster `!soon`/`!brb`/`!end` scene routes;
    - moderator `!clip`;
    - broadcaster `!watch`, via the watch module.
  - **Grants.** Explicit grants for every action used under `brain` and
    `brain.watch`: `chat.read`, `chat.write`, `users.*` as used,
    `audio.play`, `audio.speak`, `stream.scene.set`, `stream.clip.create`,
    `memory.recall`, `memory.record`, `moderation.request` and
    `screen.capture` (watch).
  - **Secrets.** Only `${NAME}` references, 0 literal secrets.
  - **Phase 2 profiles.** The three phase 2 profiles are not touched.
  - **`tests/test_presence_pack.py`** starts the profile through the same
    `_activate_example` path as `tests/test_examples.py`, with a scripted
    model and fake transports.
  - **`tests/test_profiles.py`** adds the `--check-config` checks.
- **Dependencies:** [P5, P7, P9, P13, P15, P17]
- **Tests:**
  - AC6: every observation row, from raid through the broadcaster's
    `!watch`, gives 1 `watch.state active`. The profile has 0 literal
    credentials: the hygiene `SECRET_SETTING` regex over the file is empty,
    and every credential path is a `${…}`.
  - `--check-config presence.yaml.example` exits 0 and opens no socket.
  - AC2 check-config half: temporary variants with a 2001-character
    persona, 17 routes, a duplicate route name and a 33-character command
    each exit 2 naming the field.
  - AC30 check-config half: the five invalid watch variants each exit 2
    naming the field.
- **Risks:**
  - A new `*.example` can ripple into tests that iterate profiles
    (`_environment()` in `tests/test_integration.py` resolves each
    variable). Mitigation: the presence scenario supplies its own
    environment. If a test that globs profiles breaks, fix only its fixture
    and flag it (the phase 2 precedent).
  - AC42 forbids touching the phase 2 profiles. The P26 gate diffs them.

### P23: Packaging and catalog — final phase 3 values of the allowlisted assertions (R8; AC40)
- **Files:** [pyproject.toml, tests/test_examples.py, tests/test_profiles.py]
- **Description:**
  - **`pyproject.toml`.** Verify that it holds exactly 17 `modules.<name>`
    package-data lines (6 added by P9, P10, P14, P16, P18 and P20) and that
    `dependencies` is exactly `aiohttp` and `PyYAML`.
  - **Final values of the allowlisted assertions.**
    - `test_manifests_are_unique_and_have_coherent_capabilities`: 17
      manifests, action names unique except `chat.write`, which is declared
      by exactly twitch, kick and youtube; the twitch keys as of P7.
    - `test_ac30_the_chat_only_profile_starts_and_runs_the_phase_1_scenario`:
      13 declared action names.
    - `test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`:
      17.
    - `test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency`:
      17 lines, with the dependency assertion unchanged.
  - **Scope.** Only those assertions change, and their docstrings name
    phase 3.
- **Dependencies:** [P9, P10, P11, P14, P16, P19, P21]
- **Tests:** the four rewritten tests plus
  `tests/test_main.py::test_pyproject_ships_core_and_modules_with_every_manifest`
  (unchanged) pass. The clean-install test discovers 17 manifests.
- **Risks:** a running value left by an earlier step could leave an
  assertion looser than the final one (for example "≥ 12"). Mitigation: the
  gate compares these four assertions against the spec's literal numbers.

### P24: Opt-in real trials `tests/test_phase3_trials.py` (R8; AC41 trial source)
- **Files:** [tests/test_phase3_trials.py]
- **Description:** Six trials, each skipped with an explicit reason unless
its environment variables are set:
  - **The six trials:**
    - `clips de plateforme`: Twitch clip on a live channel;
    - `modération de plateforme`: a Twitch delete in `act` on a test
      message;
    - `notifications communautaires`: a Twitch follow/raid notice observed;
    - `Kick`: a signed webhook received plus a chat send;
    - `YouTube`: token refresh, one poll and one send under the quota;
    - `veille d'écran`: watch ticks driving a real `screen.capture`.
  - **Output.** Each prints one line
    `PHASE3-TRIAL <name> commit=<sha> outcome=<...> date=<YYYY-MM-DD>`
    with no secret. Credential values are read from the environment and
    never printed.
  - **Isolation.** No trial runs by default, and none uses a positive
    `asyncio.sleep`. Real waiting uses the platform's own responses bounded
    by `asyncio.wait_for` on real transports, which is allowed only here and
    inside the opt-in gate.
- **Dependencies:** [P19, P21, P22]
- **Tests:**
  - Without the variables, the file collects 6 tests, all skipped, each
    reason naming its variables.
  - The sleep hygiene check (`test_ac45_the_suite_makes_no_positive_duration_sleep_call`)
    still passes over the new file.
- **Risks:**
  - The hygiene sleep scan covers `tests/`. Mitigation: bound waits with
    injected-clock loops or `wait_for` timeouts, never `sleep(n > 0)`.
  - A real run needs live channels. When unavailable, the README row says
    "Non exécuté" with the reason.

### P25: README phase 3 section and hygiene checks (R8, R1-table; AC7, AC41)
- **Files:** [docs/README.md, tests/test_hygiene.py]
- **Description:** Add a `## Phase 3` section in the phase 1/2 style, with
these subsections:
  - **Versioning.** Each of the 6 allowlisted tests, named with its fate.
  - **Presence table.** At least 11 rows (welcome, thanks, summary,
    translation, polls, scenes, voice, jingles, screen reactions, clips,
    memory), plus rows for channel-point redemptions, clip-link
    announcement and anonymous-gifter thanks. The "configuration ou code"
    cell names the module. Chat-command polls and channel-point redemptions
    say "code".
  - **Platform capability matrix.** Named API limits:
    - Kick public-HTTPS webhook reachability, timeout-only moderation and
      minute rounding;
    - YouTube daily quota, polling, 200-character sends;
    - Twitch scopes (P7, P8);
    - no clip or poll API on Kick or YouTube.
  - **Memory model.** Format, bounds, eviction order, erasure paths, and the
    honest limits: plain unencrypted files, per-platform-and-channel
    identities never merged, a crash may lose the last write, and the
    lone-note rule from P10.
  - **Moderation.** The modes with the default `alert` and the strict rules
    (and the P14 index limit).
  - **Watch trigger.**
  - **Trial table.** Exactly six rows in R8 order, each with commit, settings
    shape (`${…}` only), outcome (`PHASE3-TRIAL …` or `Non exécuté — <reason>`),
    limits and date. It names `tests/test_phase3_trials.py`.
  - **`tests/test_hygiene.py`** gains, reusing `_table_cells` and
    `_allowlisted_tests`:
    - the six rows, each with 5 non-empty fields, a 7–40-hex commit, no
      `SECRET_SETTING` match, `PHASE3-TRIAL` or `Non exécuté` in the
      outcome, and a date matching `YYYY-MM-DD`;
    - the section names the trials file;
    - the versioning subsection names every test of
      `docs/campaigns/phase3/spec.md`'s allowlist;
    - the presence table has at least 11 rows with non-empty cells, and
      polls and redemptions say "code".
- **Dependencies:** [P23, P24]
- **Tests:** the new hygiene tests (AC7, AC41) plus a parser self-test on
  samples, like `test_the_phase_2_readme_checks_read_the_documents_shape`.
- **Risks:**
  - The hygiene test must read the phase 3 archived spec
    (`docs/campaigns/phase3/spec.md`), not the root `spec.md`, which the
    implementation never modifies. Confirm that the archived copy carries
    the same allowlist, and re-archive under the permitted scope if the spec
    moves.
  - The commit hash in the trial rows is the commit the trials ran on.
    "Non exécuté" rows still need a hash (the base commit).

### P26: Full-branch review gate before PR delivery (R1–R8; AC39-scan, AC40, AC42, cross-file integration)
- **Files:** [docs/campaigns/phase3/gate-review.md]
- **Description:** Review the **entire branch diff** against the phase 3
base commit, reading all changed files together, not commit by commit.
  - **Why per-commit review is not enough.** Each step was reviewed alone.
    Cross-file contracts can be individually correct yet jointly broken:
    - the notice `kind` written by twitch/kick/youtube versus read by
      triggers, brain routes and the command consumers (decision 2);
    - the principal derived in the brain versus the rules in
      `presence.yaml.example`;
    - `conversation_id` produced by the brain versus parsed by
      `viewer_memory`;
    - the `moderation` service surface published by three platforms versus
      consumed by `moderation`;
    - the `clip` service surface versus `clips`;
    - `model_proposable` from loader to brain;
    - the capability view reasons across `clips` and `stream_control`.

    Component decomposition is not integration, so only a whole-branch read
    catches these.
  - **Checklist**, recorded in `docs/campaigns/phase3/gate-review.md`:
    1. `git diff --stat <base>..HEAD` touches only spec targets and
       `docs/campaigns/phase3/`.
    2. AC42: `git diff <base> -- config.yaml.example config.server.yaml.example agent.yaml.example`
       is empty, and so are `docs/design-v2.md`, `docs/proxy-protocol.md`
       and the root `spec.md`/`plan.md` of the MVP as the implementation
       sees them.
    3. AC39: a case-insensitive word grep of `core/` for kick, youtube,
       twitch and the module names is empty.
    4. AC40: pyproject has 17 lines, only `aiohttp` and `PyYAML`.
    5. The full suite passes in the project virtualenv with 0
       positive-duration sleeps. Record the pass/skip counts against phase
       2's 1891/12.
    6. Every requirement row of the coverage table below has a passing test.
    7. No permanent-ban request can be built (grep `permanent`, a ban
       without duration).
    8. No credential appears in traces (`trace_texts` scans exist for clips,
       moderation, kick and youtube).
    9. Every caller-table row was migrated.
    10. Every deviation flagged by a step (fixture ripples, spec gaps such
        as the P21 poll reason) is either resolved or escalated.
  - **Verdict.** APPROVE or REQUEST_CHANGES with file:line findings. Fixes
    loop back to the owning step id.
- **Dependencies:** [P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14, P15, P16, P17, P18, P19, P20, P21, P22, P23, P24, P25]
- **Tests:** the gate runs the whole suite (`pytest -q`) and the greps
  above. It writes no production code.
- **Risks:** the gate can pass on a green suite while a contract is only
  tested on the fake platform. Mitigation: item 6 requires each AC to cite
  a test on the real module named by the AC (twitch, kick, youtube).

## Caller tables

### `ActionSpec.model_proposable` (P1, P3, P6)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/contracts.py | `ActionSpec.__post_init__` | New field, default `False`; refusals on read/delivery. |
| core/loader.py | `_ACTION_KEYS`, `_manifest_actions` | Accept and pass the key (P3). |
| modules/brain/__init__.py | `_offered_tools`, model-call path | Offer proposable authorized writes; run limit (P6). |
| modules/proxy/__init__.py, modules/agent_link/__init__.py | spec equality/serialization for lent actions | Both enumerate the declared fields by hand (gate F5), so P27F3 carries `model_proposable` on the wire: sent as `true` only when set, rebuilt as `false` when absent (protocol §18); a proposable remote spec now compares equal. |
| modules/moderation/module.yaml | manifest | The only `true` (P14). |
| tests/test_contracts.py | spec construction | Unchanged plus new refusals. |

### Built-in trigger type `event_kind` and `payload.kind` (P2)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/triggers.py | `BUILTIN_TRIGGER_TYPES`, `_normalize`, `_evaluate_rule`, `_validate_builtin_rule` | Add the type and kind reading; the other three are unchanged. |
| modules/twitch/module.yaml, tests/fixtures/modules/fakeplatform/module.yaml | `triggers.types` | Declare `event_kind` (P7, P4). |
| modules/kick/module.yaml, modules/youtube/module.yaml, modules/watch/module.yaml | `triggers.types` | Declared at creation (P18, P20, P16). |
| modules/chat_context, modules/users | consume `channel.chat.message` | Unchanged; they see notices as chat events (decision 2). |
| tests/test_triggers.py | tests iterating `BUILTIN_TRIGGER_TYPES` | Filter by name; unaffected. |

### Brain run principal and routes (P5, P6, P13)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/brain/__init__.py | `_offered_tools`, `_delivery_call`, `_deliver_all`, `_delivery_for`, `_system_prompt`, `validate_settings` | Per-run principal and route; absent keys give phase 2 behaviour. |
| config.yaml.example, config.server.yaml.example | brain settings and grants | Unchanged (AC42); their rules name `brain`, which chat runs still use. |
| tests/test_brain.py | allowlisted manifest test | Settings set + `persona`, `routes`. |

### Runtime service kinds `clip` and `moderation` (P4, P8, P19, P21)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/twitch/__init__.py | activation | Publish `clip` and `moderation` (P8). |
| modules/kick/__init__.py, modules/youtube/__init__.py | activation | `moderation` only (P19, P21). |
| tests/fixtures/modules/fakeplatform/__init__.py | activation | The scripted services, as selected by `services` (P4). |
| modules/clips/__init__.py, modules/moderation/__init__.py | prepare | Resolve per platform. |
| tests/test_stream_control.py | registry `entries()` equalities | Its own registry fixture with poll only; unaffected (checked in P4). |

### Catalog 11 → 17 manifests (P9, P10, P14, P16, P18, P20, P23)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| tests/test_examples.py | the two allowlisted tests | Running values per step; final values in P23. |
| tests/test_profiles.py | the two allowlisted tests | Same. |
| tests/test_twitch.py | `test_manifest_declares_twitch_source_and_sink` | Rewritten in P7. |
| tests/test_main.py | `test_pyproject_ships_core_and_modules_with_every_manifest` | Unchanged; satisfied because each module step adds its line. |

Unknown callers: none are known outside the repository. Dynamic attribute
access on `ActionSpec` is the grep blind spot the spec names. The loader is
its only constructor from manifests.

## Shared-file rule (explicit ordering edges)

Each file that several steps touch is edited in this chain order, and every
pair is linked by a dependency path:

- `modules/brain/__init__.py`: P5 → P6 → P13.
- `modules/twitch/__init__.py`: P7 → P8.
- `modules/viewer_memory/__init__.py`: P10 → P11.
- `modules/moderation/__init__.py`: P14 → P15.
- `modules/watch/__init__.py`: P16 → P17.
- `modules/kick/__init__.py`: P18 → P19.
- `modules/youtube/__init__.py`: P20 → P21.
- `tests/conftest.py`: P4 → P8 → P18 → P20.
- `tests/test_contracts.py`: P1 → P3.
- `tests/test_triggers.py`: P2 → P4.
- `tests/test_clips.py`: P9 → P19.
- `tests/test_moderation.py`: P14 → P15 → P19.
- `tests/test_examples.py`: P7 → P9 → P10 → P11 → P14 → P16 → P18 → P19 → P20
  → P21 → P23.
- `tests/test_profiles.py`: P9 → P10 → P14 → P16 → P18 → P20 → P22 → P23.
- `pyproject.toml`: P9 → P10 → P14 → P16 → P18 → P20 → P23.

Where a chain link is not a direct dependency, P1…P26 run strictly in
sequence, so the order holds. The chains for `pyproject.toml`,
`tests/test_examples.py` and `tests/test_profiles.py` are also explicit
because each step there edits a running count the next one reads.

## Requirement coverage

| Req | Steps | Acceptance criteria |
|-----|-------|---------------------|
| R1 | P1, P2, P4, P5, P7, P18, P21, P22, P25 | AC1–AC7 |
| R2 | P4, P8, P9, P19 | AC8–AC12 |
| R3 | P10, P12 | AC13–AC19 |
| R4 | P11, P13 | AC20–AC23 |
| R5 | P1, P3, P6, P8, P14, P15, P19 | AC24–AC28 |
| R6 | P2, P6, P7, P16, P17, P18, P21 | AC29–AC33 |
| R7 | P18, P19, P20, P21 | AC34–AC39 |
| R8 | P22, P23, P24, P25, P26 | AC40–AC42 |

## Review findings addressed

| Finding | Where | Change |
|---------|-------|--------|
| F-P1 (blocker) absent delivery | P13 | Explicit first branch "0 delivery entries → `delivery: none`, no reply", ordered before the `confirmed` branch (which now requires a non-empty list); AC21 test for an absent delivery. |
| F-P2 (major) moderation concurrency | P14, P15 | Per-channel `asyncio.Lock` around check-and-reserve; counters reserved before the send, never rolled back; request outside the lock; P15 approvals and `auto_apply` use the same function and pop the proposal under the lock; `asyncio.gather` tests in both steps. |
| F-P3 (major) kick catalog assertion | P19, shared-file rule | `tests/test_examples.py` added to P19 Files; P19 rewrites the `chat.write` multiplicity assertion to {twitch, kick}; the chain now includes P19. |
| F-P4 (minor) memory argument contracts | Decision 6, P11 | Decision 6 separates `memory.recall` (`{}`) from `memory.record` (exchange data only, no identity property, `additionalProperties: false`); P11 tests both refusals. |

## Ordering rationale

- **P1–P3: the core contracts come first.** Every module declares
  `event_kind` or `model_proposable`, or emits a `kind`, and the loader
  refuses unknown manifest keys and trigger types. So the vocabulary (P1),
  the trigger type (P2) and the loader key (P3) must exist before any
  manifest uses them.
- **P4: the fixture platform follows immediately.** The spec requires every
  contract to be exercised on a second platform. The fake platform's notices
  and scripted `clip`/`moderation` services are the test bed for the brain,
  clips and moderation steps, and they let those steps run before the real
  platforms exist.
- **P5 and P6 before twitch.** Brain persona/routes and the run principal
  can then be proven on the fake platform. P6 comes before any module that
  relies on `brain.watch` (P17) or on proposable offers (P14).
- **P7 and P8 before clips and moderation.** P7 adds twitch notices, and P8
  publishes twitch's services, so clips (P9) and moderation (P14) can be
  tested against twitch as well as the fake platform.
- **Viewer memory: P10 → P11 → P12, then P13.** The store must exist before
  its actions, and the actions before the CLI that reuses the naming. The
  brain's post-delivery record (P13) needs the real `memory.record` (P11)
  and the per-run principal (P6).
- **P14 → P15.** Moderation's strict-rule application path (P14) is reused
  by propose, approval and auto-apply (P15).
- **P16 → P17.** The watch session and cadence logic (P16) is testable
  alone. The admission, in-flight and principal integration (P17) needs P6.
- **P18–P21: Kick and YouTube come after the contracts they must speak.**
  Their capability-view halves of AC12, AC27 and AC39 (P19, P21) need the
  `clips` and `moderation` consumers (P9, P14). YouTube's send half of AC37
  needs `chat.write` (P21) on top of the ledger (P20).
- **P22: the presence profile** composes every module, so it follows all of
  them.
- **P23–P25: packaging, trials and documentation.** Final packaging values
  (P23) come when all six manifests exist. The trials (P24) come before the
  README (P25), whose trial table cites their output.
- **P26 last.** The full-branch review gate inspects the whole diff at once,
  because per-step reviews cannot see cross-file contract drift.
