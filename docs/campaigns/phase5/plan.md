---
spec: "phase5-automation-bridge"
version: "1.2"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: true
---

# Implementation Plan

Phase 5 has two parts. The first is a vendor-free kernel rule for **input ownership** (R2, R3), with the `cue` event kind (A5). The second is one optional **bridge module**, `modules/streamerbot` (R1, R4–R7). On top of those come the declared trial gate (R8), the documentation of every combination (R9) and the static constraints (R10).

## Design decisions made by this plan

Each decision below is cited again in the step that implements it. Every one is flagged here so none of them is a silent choice.

- **D1. `chat` family membership.** The `chat` family covers the kinds `message` plus the 7 `PLATFORM_NOTICE_KINDS`. It does **not** cover `watch_tick` or `cue`.
  - This is A4's parenthetical, "ordinary messages and platform notices", taken literally.
  - `watch_tick` events are never published on the bus (`modules/watch/__init__.py:40-48`), but the `watch` module evaluates them through the shared trigger engine for channels whose `chat` pair has an owner. Counting `watch_tick` as `chat` would make the guard reject every tick of an undeclared module, which breaks R1.
  - Constant: `CHAT_FAMILY_KINDS` in `core/contracts.py`.

- **D2. Resolution domain and specificity.** Ownership is resolved for each family F over a set of pairs. That set is `{(F,'*')}`, plus every `(F,P)` that an enabled module declares, plus every `(F,P)` key in `inputs.F`. A lookup for a platform outside the domain uses the `(F,'*')` row. The resolution order for one pair is:
  1. the explicit `inputs.F.P` entry;
  2. otherwise, the explicit `inputs.F.'*'` entry;
  3. otherwise, the single enabled module that declares `(F,P)` concretely with `default: true`;
  4. otherwise, the single enabled module that declares `(F,'*')` with `default: true` (the wildcard fallback);
  5. otherwise, the pair is unowned.

  Two default declarers at the **same** specificity are refused (R2). A concrete declarer beats a wildcard declarer. An explicit owner named under `'*'` must itself declare `(F,'*')`. Otherwise the refusal names the family, `*` and the value.

- **D3. The guard is per module and covers every manifest version.** `ModuleContext.bus` (and also `triggers` and `chat`) returns a module-scoped facade whenever the loader installed an ownership table. Without a table, the shared objects are returned exactly as today.
  - **v1 modules are guarded too (R3 has no manifest-version exemption).** A v1 manifest may not declare `input_families` (refused at manifest validation), so every v1 module is an **undeclared publisher** and gets R3's compatibility rule.
  - **How the v1 route is guarded.** The v1 route `activate(bus, settings, catalog)` receives an `_OwnedBus` facade for the module **whenever the resolved table constrains any pair**, that is when at least one (family, platform) pair has a resolved owner or is explicitly set to `none` (`InputOwnership.constrains_any()`). The facade applies the undeclared-publisher predicate to `publish` and delegates every other attribute (`subscribe`, `unsubscribe`, …) to the shared bus through `__getattr__`.
  - **When the table constrains nothing,** the v1 route keeps the bare bus. This is behaviour-identical, not an exemption: an undeclared publisher's events are delivered for every unowned pair, and in that table every pair is unowned. It also keeps `tests/test_loader.py:1345` (`received_bus is context.bus`, AC26 of an earlier phase) green without adapting it, because that test enables one v1 module and no declarer.
  - No shipped module is v1. The guarded v1 route is written in the design addendum (P21).

- **D4. Counting.** The `input_ownership_rejections` counter is a new member of `DIAGNOSTIC_COUNTERS`. It is incremented once per rejected **bus publication**.
  - A rejected `triggers.evaluate` is refused without counting, and so is a rejected `chat.append`. Each returns a refused `TriggerDecision` with reason `input_not_owned`, or drops the append.
  - Both share the once-per-(module, family, platform) diagnostic. The diagnostic is the trace `input.ownership.rejected`, value-free: module, family, platform.
  - This keeps AC13 exact: 3 publications give a counter of 3.

- **D5. Family-gated binding needs an exclusion.** `Destination` cannot express "every platform except twitch". R3 requires two things:
  - the `*` owner serves every platform that is not assigned elsewhere;
  - `AmbiguousBindingError` stays unreachable.

  So `core/actions.py` gains an **additive** field `ActionBinding.excluded_platforms: frozenset[str] = frozenset()`, and `ActionRegistry.bind(..., excluded_platforms=())` gains a matching keyword. Overlap and containment ignore the excluded platforms.
  - Only the runtime's `ModuleActions` facade passes it, for a gated action.
  - **Deviation flagged.** `core/actions.py` is not in the spec's `targets`, and the spec's cost row says "5 core files". The plan touches 7 core files (D5 and D6).

- **D6. Anti-echo through the engine.** `TriggerEngine` takes `companion_identity` only at construction, and `core/main.py:1112` builds it without one. AC33 requires the **engine's** anti-echo invariant to reject an instance chat event authored by the configured identity.
  - `core/triggers.py` gains an additive method `TriggerEngine.add_companion_identities(identities)`. It is idempotent, case-folds each identity, never removes one, and is vendor-free.
  - The bridge calls it at activation, before any evaluation.
  - **Deviation flagged**: the spec's caller table says `core/triggers.py` needs no code change.

- **D7. The owned view reaches an ownership-aware settings hook.** AC33 refuses at `--check-config` when the bridge owns a `chat` pair without an identity for it. The settings hook alone cannot see ownership.
  - The loader resolves ownership **before** settings validation.
  - It then calls a module's validator as `validator(settings, owned_inputs=view)`, but only when the hook's signature declares a keyword parameter `owned_inputs`. This is checked with `inspect.signature`.
  - The 17 existing hooks are called exactly as today.

- **D8. Uncertain outcomes reuse existing mechanisms, around an explicit transmission boundary.**
  - **Not emitted until the boundary.** The run provider calls `invocation.mark_not_emitted()` as its first statement, before any `await`. Without it, a `write` with an absent emission becomes `external_unknown` (`core/actions.py:1861 _uncertain_status`), which would misreport a pre-send deadline or cancellation.
  - **The boundary is the hand-off of the frame bytes to the transport.** The frame is fully serialised and every decision is taken first. Then, in one synchronous segment with **no `await` between them**, the provider (1) runs the *pre-write check*, (2) calls `invocation.mark_emitted()`, (3) calls `ws.send_str(frame)` and awaits it. Awaiting the acknowledgement comes after.
  - **Why marking before the call is sound here, and not "marked but never sent" (round-2 finding P6).** Read in the installed aiohttp 3.14.3 (`aiohttp/_websocket/writer.py`, `WebSocketWriter.send_frame` and `_write_websocket_frame`), with `ws_connect(..., compress=0)`:
    - Awaiting a coroutine runs it synchronously up to its first suspension. In the uncompressed branch, `send_frame` has **no `await` before `self.transport.write(...)`**; its only `await` (`protocol._drain_helper()`, flow control) comes after the write.
    - Its only pre-write failures are two raises of `ClientConnectionResetError("Cannot write to closing transport")`: the writer's `_closing` flag and `transport.is_closing()`.
    - asyncio's `_SelectorSocketTransport.write` does not raise on a lost connection (it counts and returns). It raises only on `write_eof`/sendfile misuse, which the bridge never does.
    - So the pre-write check (1) tests exactly those conditions in the same synchronous segment: the bridge's own `connected` flag (cleared before the bridge ever calls `ws.close()`), `ws.closed` (aiohttp sets it before it calls the writer's `close()`, the only place the writer's `_closing` is set), and the protocol transport is present and not `is_closing()`. With no `await` between (1) and the write, and transport state only changing in event-loop callbacks, none of these can become true between the check and the write. Once `mark_emitted()` has run, the bytes **are** handed to the transport.
  - **Send failure before the frame leaves.** If the pre-write check fails, nothing was marked and no byte left: the provider returns `refused provider_not_ready` (the connection loss is the "bridge not ready" row), emission stays `not_emitted`, and the bridge goes not-ready. This mapping is flagged as a decision of this plan.
  - **Failure after the mark.** Any exception (including `ClientConnectionResetError` from the drain) or `CancelledError` raised by the awaited send, after `mark_emitted()`, necessarily postdates the hand-off. It maps to `external_unknown`, cause `acknowledgement_lost`, and never to `refused`/`timeout`/`cancelled`.
  - **Implementation check, guarded by a canary test.** P15 adds a test that parses the installed `WebSocketWriter.send_frame` with `ast`. It asserts that in the uncompressed branch no `Await` comes before the `_write_websocket_frame` call, and that the only pre-write `raise` statements are the closing checks. A second test asserts the bridge passes `compress=0`. If an aiohttp upgrade breaks the canary, the build must switch to the fallback route: a small module-local writer that writes the already-built masked frame synchronously through the protocol transport, then marks. The canary's failure message names that route. The step records in its commit message which route was taken.
  - Cancellation after the frame was sent is answered with a provider-authored interruption record carrying `cause: acknowledgement_lost`. This follows the precedent of `modules/clips/__init__.py:709-715 _cancellation`, adopted by `core/actions.py:1399/1444`.
  - The bridge's own background task still propagates `CancelledError` (R6).

## Review findings addressed (findings.json)

| Finding | Summary | Resolved in |
|---|---|---|
| P1 (blocker) | v1 undeclared publishers were outside the guard | D3 rewritten; P2 adds `constrains_any()`; P4 guards the v1 route and adds AC44 tests for a v1 publisher |
| P2 (major) | Gated-binding rewrite overlapped a `*` owner's wildcard with its own concrete rows | P5 rewrite rule: a `*` owner binds only the wildcard (with exclusions); concrete destinations only for platforms whose owner differs from the `*` owner; pairwise non-overlap test over the AC14 shape |
| P3 (blocker) | P14 tests needed P15/P18 behaviour | P14 keeps only what it implements; the AC3 `users.read` clause moves to P18, the AC28 in-flight clause moves to P15 |
| P4 (major) | P5 and P6 edit the same test file without a dependency | P6 depends on [P5] |
| P5 (major) | AC37 docs guard would be a 9th node in the 8-node trial file | AC37 moves to `tests/test_phase5_docs_status.py` (P20, P21, P22) |
| P6 (major) | Emission marked before send with no transmission boundary | D8 rewritten; P15 implements the boundary and tests the interval before it |
| P6, round 2 (major) | Marking emitted before the send leaves a "marked but not sent" gap on a pre-write failure or cancellation | D8: pre-write check mirroring aiohttp 3.14.3's only pre-write failures, run in the same synchronous segment as the mark and the write (verified: no `await` before `transport.write`); post-mark failures map to `acknowledgement_lost`; canary test pins aiohttp's behaviour, with a module-local writer as fallback. P15 tests a pre-write failure, cancellation and deadline while the hook is blocked, and a post-write interruption in the drain |

## Steps

### P1: Kernel vocabulary — input families, `cue` kind, counter, declaration contract (R2, R3, A4, A5, R10; allowlisted test_contracts)
- **Files:** [`core/contracts.py`, `tests/test_contracts.py`, `tests/test_input_ownership.py`]
- **Description:** Non-trivial: it is a public data-format change to the closed `EVENT_KINDS` vocabulary. Additive changes to `core/contracts.py`:
  - **`cue` kind.** `EVENT_KINDS` gets `"cue"` appended as the 10th member, after `watch_tick`, so the existing indices keep their order. `EVENT_KIND_SET` follows from it.
  - **`PLATFORM_NOTICE_KINDS`** becomes `tuple(k for k in EVENT_KINDS if k not in ("message", "watch_tick", "cue"))`. Its 7 members are unchanged.
  - **Family vocabulary.**
    - `INPUT_FAMILIES = ("chat", "viewers", "cues")` and `INPUT_FAMILY_SET`.
    - `INPUT_OWNER_NONE = "none"`.
    - `CHAT_FAMILY_KINDS = ("message", *PLATFORM_NOTICE_KINDS)` (D1).
    - `CUE_KIND = "cue"`.
    - `input_family_of_event(event) -> tuple[str, str] | None`. It returns `(family, platform)` for a `channel.chat.message` whose `payload.kind` (default `message`) is in `CHAT_FAMILY_KINDS` (family `chat`) or is `cue` (family `cues`). It returns `None` otherwise: `watch_tick`, other event types, or a malformed payload. The platform is read from `payload.platform`.
  - **Gated actions.** `INPUT_FAMILY_GATED_ACTIONS = MappingProxyType({"viewers": "users.read"})`. `users.read` is an action name already present in `core` (no vendor and no platform), so R10 and AC11 hold.
  - **Counter and trace.** `COUNTER_INPUT_OWNERSHIP_REJECTIONS = "input_ownership_rejections"` is added to `DIAGNOSTIC_COUNTERS`. Add `TRACE_INPUT_OWNERSHIP_REJECTED = "input.ownership.rejected"`.
  - **Declaration contract.** Add a frozen `InputFamilyDeclaration(family, platforms: tuple[str, ...], default: bool)` and `parse_input_families(value, field) -> tuple[InputFamilyDeclaration, ...]`. The parser refuses:
    - a non-list value;
    - an entry with keys other than exactly `family`/`platforms`/`default`;
    - a family outside the vocabulary;
    - an empty platform list, a non-text platform, or a `*` mixed with concrete names;
    - a duplicated (family, platform) within one manifest;
    - a non-bool `default`.

    Each refusal is a `ContractError` naming the field path.
  - Export every new name in `__all__`.

  Adapt `tests/test_contracts.py` for the two allowlisted tests only:
  - `test_event_kind_vocabulary_is_the_nine_names_in_order` now asserts the 10 names, `cue` last.
  - `test_platform_notice_kinds_exclude_message_and_watch_tick` additionally asserts that `cue` is absent and the 7 members are unchanged.
- **Dependencies:** []
- **Tests:** In `tests/test_input_ownership.py` (new), contract unit tests:
  - `parse_input_families` accepts the 4 shipped shapes, including `['*']`, and refuses each malformed case with the field named.
  - `input_family_of_event` maps `message` and each notice kind to `chat`, `cue` to `cues`, and `watch_tick` to `None`.
  - `"cue" not in PLATFORM_NOTICE_KINDS`.
  - A fresh `Counters()` exposes `input_ownership_rejections == 0`.
  - The vendor/module-name grep of `core/contracts.py` returns 0 (AC11, partial).

  Run the full suite. Expect only the 2 allowlisted contract tests to need the adaptation above. Record any other failure: per the spec's blind spot, for example a test asserting the exact `DIAGNOSTIC_COUNTERS` tuple. Extend the allowlist only with a test whose assertion R3 contradicts, and name it in the P22 report.
- **Risks:**
  - Consumers that iterate `EVENT_KINDS` will see `cue` automatically: the `core/triggers.py` `event_kind` enum, the brain's route validation, and the module-local mappings in kick and youtube (which use their own names). P11 covers the brain.
  - A consumer that treats an unknown `kind` like a `message` could act on cues: `viewer_memory`, `moderation`, `watch`, `chat_context`. P11 checks each of them.

### P2: Loader — `input_families` manifest key and ownership resolution before any preparation (R2; AC6, AC7, AC8, AC9 resolution)
- **Files:** [`core/loader.py`, `tests/test_input_ownership.py`]
- **Description:** Non-trivial: branching resolution, and a refusal path before any effect.
  - **Manifest.** `_validate_manifest` accepts the optional `input_families` key through `parse_input_families`. `ManifestDeclaration` gains `input_families: tuple[InputFamilyDeclaration, ...] = ()` and `declares_input_families: bool`, where "absent" is distinct from `[]`, as R3's undeclared-publisher rule needs. A v1 manifest that carries the key is refused (D3).
  - **Pure resolver.** A new pure function, `resolve_input_ownership(enabled: Sequence[str], declarations: Mapping[str, ManifestDeclaration], inputs: Mapping | None) -> InputOwnership`, implements D2. `InputOwnership` is frozen and exposes:
    - `owner(family, platform) -> str | None`. `None` means unowned, both for an explicit `none` and for a pair nobody owns.
    - `origin(family, platform)`: `"explicit"`, `"default"` or `"unowned"`.
    - `candidates(family, platform) -> tuple[str, ...]`: every enabled declarer.
    - `rows()`: the resolved domain, sorted.
    - `view_for(module) -> OwnedInputs`.
    - `excluded(family) -> frozenset[str]`: the concrete platforms whose owner differs from the `(family,'*')` owner.
    - `is_set_none(family, platform)`.
    - `constrains_any() -> bool`: true when at least one resolved pair has an owner or is explicitly `none` (D3, used by P4's v1 route).
  - **`OwnedInputs`.** Defined in `core/loader.py` and re-exported for runtime use; P4 moves it into `core/runtime.py` if import order requires. It is a read-only frozen view:
    - `pairs: frozenset[tuple[str, str]]`;
    - `owns(family, platform)`, which answers `(family, '*')` ownership minus `excluded(family)`;
    - `platforms(family)`;
    - the constructor `OwnedInputs.unrestricted()`, the view used when no ownership table is installed (P4, P8).
  - **Refusals.** All are collected first, then raised as one `ModuleLoadError` whose `diagnostics` lists one value-free line per refusal. Each line names `family`, `platform` and the module names or value; none names a configured secret.
    - (a) Two or more same-specificity `default: true` declarers and no explicit entry. The line is `inputs: family 'chat': platform 'x': owned by default by both 'a' and 'b'; set inputs.chat.x`.
    - (b) An owner value that is a list of two or more modules. The line names both modules.
    - (c) An owner that is not enabled.
    - (d) An owner that does not declare that family for that platform. For the `'*'` key, the owner must declare `'*'`.
    - (e) A family outside the vocabulary.
    - (f) A malformed `inputs` entry: a non-mapping, or a non-text platform key.
  - **Order in `activate_enabled`.** The resolver is called in `activate_enabled` immediately after `_validate_compatibility` and **before** secrets resolution, settings validation, declaration registration and any `prepare`/`activate`. The result is stored on `self.input_ownership`.
  - **Ownership-aware settings hook.** `_validate_settings` passes `owned_inputs=self.input_ownership.view_for(name)` to a hook only when the hook declares that keyword (D7).
  - The resolver contains no platform or vendor literal (R2, AC11).
- **Dependencies:** [P1]
- **Tests:** In `tests/test_input_ownership.py`, using tmp_path test modules (`make_module`-style manifests that declare `input_families`):
  - **AC7.** Two modules both declaring (`chat`, `x`) `default: true` give exactly 1 diagnostic naming `chat`, `x` and both names. A spy `activate`/`prepare` records 0 calls, and a socket-creation spy (the phase-4 pattern in `tests/test_config_ui.py`) records 0.
  - **AC8**, with test modules standing in for `twitch` and the bridge (the shipped ones come in P8/P12):
    - an owner list of 2 is refused naming both;
    - an owner that is not enabled is refused;
    - an owner that does not declare the platform is refused;
    - `weather` is refused, naming the family, the platform and the value.
  - **Explicit and default precedence.** Explicit beats default; the `'*'` key applies to undeclared platforms; a concrete declarer beats a wildcard declarer; a wildcard-only declarer owns `x` by fallback.
  - **`none`** leaves the pair unowned (AC9 last clause).
  - `view_for` returns exactly the owned pairs, and `excluded` is correct for a `*` owner when one platform is assigned elsewhere.
  - `constrains_any()` is false with no declarer and no `inputs`, true with one `default: true` declarer, and true with only `inputs: {chat: {x: none}}`.
  - A hook with `owned_inputs` receives the view; a one-argument hook is still called with one argument.
  - A v1 manifest declaring the key is refused.
- **Risks:**
  - Diagnostics must stay value-free. An `inputs` value could be a `${NAME}`-resolved string, so it goes through the same `_value_free_reason`/redaction path the loader already uses.
  - Resolution must run in the same order for `run` and `--check-config`. P3 wires `core/main.py:_validate_enabled` to the same resolver, and the parity test there guards it.

### P3: `core/main.py` — optional top-level `inputs` block through load_config, run, check_config and --check-config (R2, R1; AC7 via CLI, AC1 shape)
- **Files:** [`core/main.py`, `tests/test_input_ownership.py`]
- **Description:** Non-trivial: a new configuration shape crosses into the loader.
  - **Structural validation.** `_validate_config` accepts an optional `inputs` key. It must be a mapping of family to a mapping of platform-or-`*` to an owner (a text, or a list of texts, so that P2's rule (b) can name both). An absent key means `{}`.
  - Family and owner semantics are left to P2's resolver, so the two refusal sources never disagree.
  - `${NAME}` resolution leaves `inputs` untouched: owner names are not secrets.
  - **`_validate_enabled`.** The function `check_config` uses (`core/main.py`) calls `loader.resolve_ownership(config)`, the same entry point `activate_enabled` uses, at the same position, so `--check-config` reports ownership diagnostics with 0 transports.
  - `run` and `main` need no signature change.
  - **Startup diagnostics.** `_startup_diagnostics` reports the loader's ownership diagnostics verbatim, one per line.
- **Dependencies:** [P2]
- **Tests:**
  - **AC7 end to end.** `--check-config` on a tmp profile with the two test modules exits 2, with exactly the 1 diagnostic and 0 sockets.
  - An `inputs` value that is not a mapping is refused naming `inputs`.
  - A profile without `inputs` checks exactly as before: same exit status and diagnostics on a tmp copy of the fixture profile used by `tests/test_main.py`.
  - **`run` / `--check-config` parity.** The same invalid profile gives the same diagnostic list through both paths, with 0 modules prepared in `run`.
- **Risks:** `load_config` is shared by the config UI and test helpers. The key is optional and defaults to absent, so the 29 `tests/test_main.py` call sites are unaffected (caller table below).

  | File | Function/Method | Migration Note |
  |------|----------------|----------------|
  | core/main.py | `run` → `load_config`, loader `activate_enabled` | None; `inputs` optional |
  | core/main.py | `check_config` → `_validate_enabled` | Calls the resolver (new step, same position as `activate_enabled`) |
  | core/main.py | `main` (`--check-config`) | None |
  | core/config_ui/__init__.py | `check_config(..., overlay_document=draft)`, `load_config` | None for signatures; the P7 table reads `inputs` |
  | tests/test_main.py, tests/test_profiles.py, tests/test_examples.py, tests/test_integration.py, tests/test_shutdown.py | `run`/`check_config`/`load_config` | None; their profiles carry no `inputs` |

### P4: Runtime guard — owned view on the module context, guarded bus/triggers/chat facades, counter and diagnostic (R3; AC13, AC44, AC9 view)
- **Files:** [`core/runtime.py`, `core/loader.py`, `tests/test_input_ownership.py`]
- **Description:** Non-trivial: a module boundary, an ordering guarantee ("before any subscriber"), and a public context API change.
  - **`for_module`.** `RuntimeContext.for_module(module, *, owned_inputs=None, ownership=None)` takes keyword-only additions, so existing calls are unchanged.
  - **`ModuleContext.inputs`.** `ModuleContext` gains a field `inputs: OwnedInputs`, exposed read-only. When `for_module` gets no ownership, it holds `OwnedInputs.unrestricted()`, which answers `owns(...) == True` for every pair, so modules built by existing harnesses keep ingesting as today (P8 relies on this). It also holds a private `_guard`.
  - **The guard.** When `ownership` is given, the `bus`, `triggers` and `chat` properties return module-scoped facades (`_OwnedBus`, `_OwnedTriggers`, `_OwnedChat`). Every other attribute is delegated through `__getattr__`. When `ownership` is `None`, the shared objects are returned, so the 35 `for_module(...)` test sites keep identity.
  - **`_OwnedBus.publish(event_type, payload, metadata)`**, for `channel.chat.message` with `input_family_of_event(...) == (F, P)`:
    - If the module **declares** `input_families`: deliver only if `ownership.owner(F,P) == module`.
    - If the module is **undeclared** (R3 compatibility rule): deliver unless `(F,P)` has a resolved owner, or `inputs` set `(F,P)` to `none` explicitly.
    - **On rejection:** call the shared `supervision.count(COUNTER_INPUT_OWNERSHIP_REJECTIONS)`. If `(module, F, P)` is not yet in a set owned by the guard, add it and emit **one** trace `input.ownership.rejected` with `{module, family, platform}`. Then return without calling the bus, so no subscriber runs.
    - Other events, and events with no family (D1), pass through untouched.
  - **`_OwnedTriggers.evaluate`** applies the same predicate. On rejection it returns a refused `TriggerDecision` with reason `input_not_owned`, built with the existing dataclass at `core/triggers.py:366`, without counting (D4), and shares the diagnostic set.
  - **`_OwnedChat.append`** drops a non-owned event's append under the same rule.
  - **Loader wiring.** In `core/loader.py`, `_activate` and `_register_declarations` (the `for_module` calls at lines 583 and 1192) pass `owned_inputs=self.input_ownership.view_for(name)` and `ownership=self.input_ownership`.
  - **v1 route (D3, finding P1).** Where the loader calls a v1 `activate(bus, settings, catalog)` (the v1 branch of `_activate`, near `core/loader.py:515-522`), it passes `_OwnedBus(shared_bus, module=name, ownership=self.input_ownership, declared=False)` when `self.input_ownership.constrains_any()`, and the bare shared bus otherwise. The facade is the same class as the v2 one, constructed directly, so the predicate, counter and once-per-(module, family, platform) diagnostic are shared code. `_OwnedBus` is defined so that it can be built without a `ModuleContext`.
  - **`RUNTIME_API` is unchanged.** The additions are optional reads.

  Caller table (`grep -rn "for_module(" core modules tests`; blind spot: dynamic attribute access on the context):

  | File | Function/Method | Migration Note |
  |------|----------------|----------------|
  | core/loader.py:583 | `_activate` | Passes `owned_inputs`, `ownership` |
  | core/loader.py:1192 | `_register_declarations` | Passes the same, so a declaration-time facade sees the view |
  | core/config_ui/__init__.py:2963, 3720, 4023 | `result.for_module` (a different method on the Check result) | Unrelated name; no change |
  | tests/* (35 call sites in 22 files) | `context.for_module(name)` | None: no `ownership` → shared objects returned, identity preserved |
  | tests/test_loader.py:1345 | v1 route `received_bus is context.bus` | Unchanged and not adapted: that test enables one v1 module and no declarer, so `constrains_any()` is false and the bare bus is passed (D3) |
  | core/loader.py (v1 branch of `_activate`) | v1 `activate(bus, settings, catalog)` | Receives `_OwnedBus` when the table constrains any pair |
- **Dependencies:** [P2, P3]
- **Tests:** In `tests/test_input_ownership.py`, with a real `EventBus`, recording subscribers standing in for the trigger engine, `users`, `chat_context` and brain, and a recording supervision:
  - **AC13.** A declared test module publishes 3 events for a pair it does not own. 0 reach any subscriber, the counter is 3, and exactly 1 `input.ownership.rejected` trace is emitted.
  - **AC44.** An undeclared module publishing 3 events for (`chat`, x):
    - with nobody declaring the pair, all 3 delivered, counter 0;
    - with a second module declaring `default: true`, 0 delivered, counter 3;
    - with `inputs: {chat: {x: none}}`, 0 delivered, counter 3.
  - **AC44 for a v1 publisher (finding P1).** The same three cases with a tmp **v1** manifest (no `manifest_version`) whose `activate(bus, …)` publishes the 3 events through the bus it received: 3/0/0 delivered and counter 0/3/3, with exactly 1 diagnostic in each rejecting case. In the first case the received bus `is` the shared bus.
  - `tests/test_loader.py` runs in full, unadapted.
  - **AC9.** The `twitch` stand-in's `context.inputs.pairs` is empty while the owner's view holds (`chat`, twitch).
  - `watch_tick` events and non-chat events pass untouched.
  - The evaluate facade returns a refused decision without counting.
  - `for_module(name)` without `ownership` still returns `bus is runtime.bus`.
- **Risks:**
  - **Ordering.** The check must run before `bus.publish` begins its handler chain. The facade returns before calling the bus, so even an audit middleware with a negative order sees nothing, which satisfies "reaches no subscriber".
  - **Identity.** A module comparing `context.bus is ...` would differ. A grep of `modules/` finds none (P22 re-greps).
  - **v1 facade completeness.** A v1 module may call any bus method. `__getattr__` delegation covers them; `publish` is the only intercepted name. A test subscribes through the facade and receives an owned event.
  - The diagnostic set is bounded by modules × families × platforms. Platforms come from events, so the set is capped at a constant (e.g. 256 entries), after which the counter alone is incremented, still never per event.

### P5: Family-gated binding — `users.read` bound only for owned platforms (R3; AC14 with test modules)
- **Files:** [`core/actions.py`, `core/runtime.py`, `tests/test_input_ownership.py`]
- **Description:** Non-trivial: a public registry API change (D5, deviation flagged).
  - **`core/actions.py`:**
    - `ActionBinding` gains `excluded_platforms: frozenset[str] = frozenset()`.
    - `ActionRegistry.bind(..., excluded_platforms: Iterable[str] = ())` and `register(..., excluded_platforms=())` pass it through.
    - The overlap test in `bind` and the containment test in `resolve` treat a binding with `platform == '*'` as not covering any excluded platform.
    - Everything else is unchanged.
  - **`ModuleActions.bind`/`register` in `core/runtime.py`.** When the action name is the value of `INPUT_FAMILY_GATED_ACTIONS[F]` and the module context carries an ownership table, the facade rewrites destinations from the spec's declared scope. The rule never emits a concrete destination that a wildcard binding of the same module already covers (finding P2):
    - **If the module owns `(F,'*')`:** exactly one `Destination('*','*',scope)` with `excluded_platforms = ownership.excluded(F)`, and **no** concrete destination. Concrete rows it also owns (for example `viewers.kick` and `viewers.youtube`, which exist in the domain because the bridge declares them, and resolve to the `*` owner by D2 step 4) are already served by the wildcard, because they are not in `excluded(F)`.
    - **Otherwise:** one `Destination(P, '*', scope)` per concrete platform P the module owns for F.
    - `excluded(F)` is exactly the concrete platforms in the domain whose owner differs from the `(F,'*')` owner, including platforms set to `none` or unowned. So each concrete platform is served by at most one binding: the wildcard owner's when it is not excluded, its own owner's concrete binding when it is.

    If the module owns no pair of F, `register` only **declares** the spec and binds nothing. Two declarers of the same spec merge destinations, per the P19-era fix noted in memory "chat-write second-declarer conflict".

  Caller table (`grep -rn "\.bind(\|\.register(\|ActionBinding(" core modules`; blind spot: providers bound through helper indirection):

  | File | Function/Method | Migration Note |
  |------|----------------|----------------|
  | core/actions.py:686 | `ActionRegistry.bind` | New keyword, default `()` |
  | core/actions.py:755 | `ActionRegistry.register` | Passes the keyword through |
  | core/actions.py:910 | `ActionRegistry.resolve` | Honours exclusions |
  | core/actions.py (1 site) | `ActionBinding(...)` | New defaulted field |
  | core/runtime.py | `ModuleActions.bind/register` | Rewrites destinations for gated actions only |
  | modules/*/__init__.py (every `self._actions.register/bind`) | — | Unchanged; only `users.read` registrations (users, bridge) take the gated path |
- **Dependencies:** [P4]
- **Tests:**
  - **AC14 with stand-ins, in the exact shipped shape.** Two test modules both register `users.read`. One declares (`viewers`, `*`) `default: true`; the other declares (`viewers`, [twitch, kick, youtube]) `default: false`, so the domain holds concrete kick and youtube rows that resolve to the `*` owner. With `inputs: {viewers: {twitch: <second>}}`: preparation raises no `AmbiguousBindingError`; the `*` owner holds exactly 1 binding (wildcard, excluding `{twitch}`) and the second exactly 1 (twitch). A call at twitch invokes only the second; calls at kick and at youtube invoke only the `*` owner (counted by `executor.provider_invocations` and per-provider spies).
  - **Non-overlap invariant.** For each of these resolved tables — no `inputs`; `twitch → second`; `twitch → second, kick → none`; `'*' → second` (second declaring `*` too); the second owning nothing — collect the bindings the facade produced and assert they are pairwise non-overlapping by the registry's own overlap test, and that every platform in the domain plus one undeclared platform resolves to at most one binding.
  - **Registry unit tests:** an excluded platform is not overlapped; `resolve` returns `None` for an excluded platform with no other binding; existing overlap refusals are unchanged when no exclusion is given.
  - A module owning nothing declares but does not bind.
  - Run `tests/test_actions.py` in full.
- **Risks:**
  - `excluded_platforms` must never be reachable from module code; it is set only by the facade. Assert this by grepping `modules/` in P22.
  - A gated action registered **before** ownership exists (a test harness) keeps today's behaviour.
  - **`none` under a wildcard owner.** `inputs: {viewers: {kick: none}}` puts kick in `excluded(viewers)`, so a kick read resolves to no binding and the executor reports its existing no-provider outcome. This is intended (the operator set the pair unowned) and is tested in the invariant list.

### P6: Engine anti-echo registration (R7; AC33 engine half — D6)
- **Files:** [`core/triggers.py`, `tests/test_input_ownership.py`]
- **Description:** Non-trivial: an invariant the type system cannot check.
  - Add `TriggerEngine.add_companion_identities(identities: Iterable[str]) -> None`. It validates each identity with `_require_text`, case-folds it, and unions it into `_companion_identities`. It is append-only and idempotent.
  - The `_OwnedTriggers` facade (P4) delegates it through `__getattr__`.
  - No vendor or platform literal.
- **Dependencies:** [P5] (P5 itself depends on P4; P6 is ordered after P5 because both edit `tests/test_input_ownership.py`, finding P4)
- **Tests:**
  - An engine with an added identity rejects an event authored by that identity, case-insensitively, through the existing anti-echo decision reason.
  - Adding the same identity twice is a no-op.
  - Engines built without the call behave as today: run `tests/test_triggers.py` in full.
- **Risks:** The identity is global to the engine. That is correct, because it is the companion's own account on that platform. A misconfigured identity could suppress a real viewer's events; the guide (P21) says so.

### P7: Configuration UI — read-only ownership table and ownership diagnostics through Check (R2, A8; AC10)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`]
- **Description:** Non-trivial: it crosses the loader/UI boundary.
  - **The table.** The core-settings page renderer gains a read-only "Input ownership" section. It is computed with `resolve_input_ownership`, using the manifests discovered by `_discover_valid` (no socket opened, phase-4 R5) and the merged profile's `inputs` block.
  - **Rows.** One row per resolved pair: family, platform, owner (or `none`/unowned), origin (`explicit`/`default`/`unowned`), and the sorted candidate set.
  - **Robustness.** When resolution fails, the section shows "ownership could not be resolved; see Check" instead of a table.
  - **Escaping.** Every cell is escaped with `esc()`.
  - **Not editable.** No form control is emitted. `inputs` is not added to the phase-4 writable scope (A8): Save keeps refusing `inputs.*` paths through the existing scope rule, so no code is needed beyond a test.
  - **Check** already runs `check_config`, so P3's diagnostics appear. Verify the diagnostic text is shown unmodified.
  - **Vendor-free.** The section names no module in code; module names appear only as data.
- **Dependencies:** [P3]
- **Tests:** In `tests/test_config_ui.py`, using the phase-4 socket-free fixture:
  - **AC10 table.** With the AC9 profile built from tmp test modules named `twitch` and a bridge stand-in, the page has a row (`chat`, twitch) with owner = bridge, origin "explicit", candidates {twitch, bridge}, rendered with 0 sockets.
  - **AC10 Check.** Check on the AC7 configuration shows the AC7 diagnostic.
  - A posted edit to `inputs.chat.twitch` is refused as out of scope.
  - Per the memory note "check-shares-restoration-not-protection", Check keeps its existing draft semantics.
- **Risks:** An exact-section-list assertion in `tests/test_config_ui.py` (the spec's blind spot) may need the new section added. Extend the allowlist only if the assertion contradicts R2, and record it in P22.

### P8: `twitch` manifest and ingestion gate (R2, R3; AC6, AC12)
- **Files:** [`modules/twitch/module.yaml`, `modules/twitch/__init__.py`, `tests/test_twitch.py`, `tests/test_examples.py`]
- **Description:** Non-trivial: it changes branching in the ingestion start.
  - **Manifest.** Add `input_families: [{family: chat, platforms: [twitch], default: true}]` with a comment.
  - **Gate.** In `modules/twitch/__init__.py`, `start_inputs` (line 1148) opens the EventSub WebSocket and every chat, notice and follow subscription only when `context.inputs.owns("chat", "twitch")`.
  - **When not owned:**
    - no socket and 0 subscriptions;
    - one value-free diagnostic, `twitch: chat ingestion owned elsewhere`;
    - the module still declares and binds `chat.write`, keeps its `handle_chat_send` subscription, and marks itself ready in `prepare` (line 1094), as today.
  - The ownership check reads only the context view: a context built without ownership owns nothing. Existing tests build the context without an ownership table, so the module must treat an **absent table** as "own everything I declare". `OwnedInputs.unrestricted()` covers this: it is the default when `for_module` gets no `ownership`.
  - **`tests/test_examples.py`** (allowlisted): add `input_families` to the expected key set for `twitch`. P9, P10 and P12 extend the same tables.
- **Dependencies:** [P4]
- **Tests:** In `tests/test_twitch.py`, using the existing fake EventSub/Helix harness:
  - **AC12.** With ownership (`chat`, twitch) → bridge stand-in, `start_inputs` opens 0 EventSub subscriptions (the fake server records 0 connections), while `chat.write` is bound, ready, and a granted `chat.write` reaches the fake Helix send endpoint once.
  - **AC5 (no fallback).** With the bridge stand-in owning the pair, the twitch notification handler is driven directly and the published event is dropped by the guard (counter +1).
  - **AC6.** `twitch` plus a bridge stand-in with no `inputs`: `twitch` owns (`chat`, twitch) by default.
  - Run `tests/test_twitch.py` in full with no ownership table, to confirm unchanged behaviour.
- **Risks:**
  - **Default when no table.** P4 must make "no ownership installed" mean unrestricted, or every existing twitch test breaks.
  - **Follow subscription.** It belongs to the notice kinds, so it is gated too.
  - **`chat.write` readiness.** It must not depend on the EventSub socket.

### P9: `kick` and `youtube` manifests and ingestion gates (R2, R3; AC12 analogue)
- **Files:** [`modules/kick/module.yaml`, `modules/kick/__init__.py`, `modules/youtube/module.yaml`, `modules/youtube/__init__.py`, `tests/test_kick.py`, `tests/test_youtube.py`, `tests/test_examples.py`]
- **Description:** Non-trivial: branching ingestion.
  - **Manifests.** Add `input_families: [{family: chat, platforms: [kick], default: true}]` for kick and the `[youtube]` equivalent for youtube.
  - **Kick.** `start_inputs` (line 1180) still binds its listener, because the webhook endpoint also serves non-chat traffic as today. The webhook handler, however, neither accepts nor publishes a chat or notice event when `context.inputs.owns("chat","kick")` is false. It answers the platform with the same success status it uses for an ignored event, so Kick does not retry, and it counts the event in the module's existing ignored-event diagnostic. This choice is flagged in the step: the alternative, not binding at all, would break webhook verification.
  - **YouTube.** `start_inputs` (line 1213) starts no chat poll when it does not own (`chat`, youtube).
  - Both keep `chat.write` bound and ready.
  - **`tests/test_examples.py`:** add the key for kick and youtube.
- **Dependencies:** [P8]
- **Tests:** In `tests/test_kick.py` and `tests/test_youtube.py`, using their existing fakes:
  - Not owning → 0 events published for a valid signed webhook, and 0 poll requests.
  - `chat.write` still reaches its fake provider.
  - Full module suites pass with no ownership table.
- **Risks:**
  - **Kick listener.** If kick's listener serves only chat, skipping the bind may be cleaner. Inspect `build_application` first: if every route is chat, skip the listener when not owned, and say so in the step's commit message.
  - **YouTube readiness.** If readiness depends on the first poll, keep it on `chat.write` readiness.

### P10: `users` — viewers family, widened coverage enum, owned-platform binding, cue authors skipped (R3, R7; AC14, AC34 last clause; allowlisted test_users)
- **Files:** [`modules/users/module.yaml`, `modules/users/__init__.py`, `tests/test_users.py`, `tests/test_examples.py`]
- **Description:** Non-trivial: the binding scope and a widened result schema, which is a data contract.
  - **Manifest.**
    - Add `input_families: [{family: viewers, platforms: ['*'], default: true}]`.
    - Widen `users.read` `result_schema.coverage.kind` from `enum: [observed_authors]` to `[observed_authors, active_viewers]`.
    - The description must fit both providers ("who was observed in chat, or who the provider reports as active").
  - **Module.**
    - `prepare` still calls `self._actions.register(spec, provider)`. The P5 facade binds it only for owned platforms, so the module needs no ownership code for the binding.
    - `observe_event` returns early for `payload.kind == "cue"`: a cue's author is never recorded.
    - Pages keep `coverage.kind: observed_authors`.
  - **Shared spec.** `_declared_users_read_spec()` stays the single parse of the manifest. The bridge (P18) reads the **users manifest's** declaration so the two specs are equal.
  - **`tests/test_users.py`** (allowlisted): `test_manifest_is_v2_and_declares_users_read_without_granting_it` asserts the 2-member enum.
  - **`tests/test_examples.py`:** add the key for users, and update `DECLARED_ACTIONS` if it carries the enum.
- **Dependencies:** [P5, P9]
- **Tests:** In `tests/test_users.py`:
  - a cue event leaves the directory unchanged;
  - pages still report `observed_authors`;
  - with ownership excluding twitch, a `users.read` at twitch is not served by users (no binding → the executor reports `no_provider` when no other provider exists);
  - the full suite passes.
- **Risks:** Declaring the same spec from two modules relies on declare-merge (memory "chat-write second-declarer conflict"). If specs differ by a byte the registry refuses, so P18 loads the spec from `modules/users/module.yaml` rather than copying it.

### P11: Brain treats `cue` like a notice for route safety and empty text; consumer audit (R7, A5; AC31)
- **Files:** [`modules/brain/__init__.py`, `tests/test_brain.py`]
- **Description:** Non-trivial: branching safety rules.
  - **Route safety.** `viewer_reachable` (line 1312) becomes `self.kinds <= frozenset((*PLATFORM_NOTICE_KINDS, CUE_KIND))`.
  - **Diagnostic.** The route-safety diagnostic at line 2667 adds "or cues".
  - **Empty text.** The empty-text check at line 4859 accepts empty text for `cue`, as for notices.
  - **Route `kinds` validation** (line 976) accepts `cue` through `EVENT_KIND_SET` automatically.
  - **No trusted roles from text.** Body text never yields a role claim; the brain already reads claims only from `author.roles` provenance.
  - **Consumer audit, recorded in the commit message:**
    - Read the `kind` handling of `viewer_memory`, `moderation`, `watch` and `chat_context` for `channel.chat.message`, and record how each treats a `cue`.
    - If one would moderate or memorise a cue author, the step does **not** silently edit that out-of-target file. It records the behaviour as a finding for P22, to be decided there with the spec author.
    - A one-line kind skip is the expected fix, and it would be flagged as scope beyond the targets.
- **Dependencies:** [P1]
- **Tests:** In `tests/test_brain.py`:
  - **AC31.** The route `match: {kinds: [cue], command: "!brb"}` with fixed delivery `{action: automation.action.run, text_argument: none, arguments: {action: "BRB"}}` passes prepare. Use a fake catalog declaring the run action's spec shape from P12, or a stand-in with `delivery: {text_argument: none}`.
  - The same route with `kinds: [message]` and no audience is still refused.
  - A cue with empty text is admitted to routing.
- **Risks:** Treating cues as trusted rests on A5: the operator's own authenticated instance. Document in the guide (P21) that anyone able to edit the instance's actions can trigger these routes.

### P12: Bridge manifest, settings validator, package data and count adaptations (R1, R4, R6, R10; AC5 schema, AC15, AC22, AC23 titles, AC41; allowlisted test_examples/test_manifest_presentation/test_profiles)
- **Files:** [`modules/streamerbot/module.yaml`, `modules/streamerbot/__init__.py`, `pyproject.toml`, `tests/test_automation_bridge.py`, `tests/test_examples.py`, `tests/test_manifest_presentation.py`, `tests/test_profiles.py`]
- **Description:** Non-trivial: a public manifest, a new action contract, and a packaging change.
  - **`module.yaml`, manifest v2:**
    - `runtime_api: 2`, `lifecycle.roles: [input]`.
    - `produces: [channel.chat.message]`.
    - `credentials: [password]`.
    - `settings_validator: validate_settings`.
  - **`settings_schema`.** Every node has a `title` and machine-readable `default` where one exists. There is **no `required`** anywhere (AC5).
    - `url`: string.
    - `password`: string.
    - `deadlines`: `connect_seconds`, `authentication_seconds`, `request_seconds`, `acknowledgement_seconds`.
    - `reconnect`: `min_delay_seconds`, `max_delay_seconds`.
    - `queue_max_events`.
    - `allowlist`: an array of `{action}`|`{group}` entries with an optional `arguments` schema.
    - `companion_identities`: platform → id.
    - `instance_identity`: platform → broadcaster account id, used to default `channel_id` and to scope `users.read`.
    - `trust_instance_roles`: bool, default false.

    The validator enforces that `url` is present even though the schema has no `required` (AC22).
  - **`input_families`:**
    - `chat` on `[twitch, kick, youtube]`, default false;
    - `viewers` on `[twitch, kick, youtube]`, default false;
    - `cues` on `['*']`, default true.
  - **`triggers` declaration.** The trigger types the native chat inputs declare, plus `event_kind`. The default policy is `any_of`: `event_kind: [cue]` OR `keyword: ['${companion_name}']`. These are existing rule types (`core/triggers.py:128-131`, `COMBINATION_OPERATORS`).
  - **`actions`:**
    - `automation.action.run`: `nature: write`, `idempotency: none`, `required_permissions: [automation.run]`, `destinations: [{platform: '*', channel_id: '*', scope: automation}]`, `delivery: {text_argument: none}`, not model-proposable.
    - Its `argument_schema` is `{type: object, properties: {action: {type: string, minLength: 1, maxLength: 128}, arguments: {type: object}}, required: [action], additionalProperties: false}`.
    - `timeout_seconds` is a fixed value; the validator refuses an `acknowledgement_seconds` greater than it minus a margin (R4: timeout ≥ acknowledgement deadline).
    - The description contains the sentence "Acceptance by the automation application is not a confirmation that the action ran."
    - `users.read` is **not** copied into the manifest. It is declared at prepare from the users manifest (P18), and the manifest comment says so, so the spec stays single-sourced. If the loader requires every registered action to be manifest-declared, the declaration is instead copied verbatim and a test asserts equality with the users manifest (decide at build by running `tests/test_loader.py`).
  - **`__init__.py` (this step only):**
    - `validate_settings(settings, *, owned_inputs=None) -> list[str]` (D7). It produces value-free diagnostics `module 'streamerbot': field 'X': reason` for: a missing `url`; a scheme not `ws`/`wss`; a non-loopback host without `wss` (reusing the loopback rule of `modules/agent_link/__init__.py`, read and mirrored, not imported if it is private); deadlines and delays that are non-positive or non-finite; `min_delay > max_delay`; a non-positive integer `queue_max_events`; duplicate `action:` names or `group:` names (AC45 first half); an entry with both or neither key; an invalid `arguments` schema (via `validate_schema`); and, when `owned_inputs` owns (`chat`, P), a missing `companion_identities.P`, naming the platform (AC33 first half).
    - An `activate` returning a handle whose `prepare` registers the run action with a provider that answers `refused provider_not_ready`, plus `close`. P14 replaces it.
  - **No defaults.** There is no default endpoint, port, password, version or action name anywhere (R6).
  - **`pyproject.toml`:** add `"modules.streamerbot" = ["module.yaml"]`. Dependencies are unchanged (memory "phase1-module-step-pyproject-line").
  - **Allowlisted count adaptations, 17 → 18:**
    - `tests/test_examples.py`: `MODULE_NAMES`, `EXPECTED_MANIFESTS`, `DECLARATION_KEYS` with `input_families`, `DECLARED_ACTIONS` with `automation.action.run`.
    - `tests/test_manifest_presentation.py`: the count.
    - `tests/test_profiles.py`: both count tests.
- **Dependencies:** [P2, P6, P10]
- **Tests:** In `tests/test_automation_bridge.py` (new):
  - **AC15.** The discovered spec fields, exactly. The schema accepts `{action:"a"}` and refuses a missing `action`, an extra key, and lengths 0 and 129. The description contains the test-defined constant sentence.
  - **AC22.** `ws://192.0.2.1:9/` is refused; `ws://127.0.0.1:9/` and `wss://192.0.2.1:9/` are accepted. A missing `url`, a `0` deadline and a `nan` deadline are each refused with one diagnostic naming the field and carrying no value.
  - **AC23 (titles).** `tests/test_manifest_presentation.py` passes for 18, and `password` is in `credentials`.
  - **AC5 (schema).** The schema has no `required` key at any depth.
  - **AC45 (validator half).** Duplicate entries are refused.
  - **AC33 (validator half).** `owned_inputs` owning (`chat`, twitch) without `companion_identities.twitch` gives one diagnostic naming `twitch`.
  - **AC41.** The dependencies are exactly `aiohttp` and `PyYAML`, and the package-data line is present. The installed-distribution test discovers 18.
  - `grep -ri` of `core/` for the module and vendor names returns 0 (AC11, re-run).
- **Risks:**
  - The phase-4 `default` annotation must not create a hidden default URL: `url` has no `default`.
  - The manifest's YAML comments must not use the vendor name inside `core/` (they live in `modules/`, which is allowed).
  - AC5's "no required" depends on the validator, not the schema, refusing a missing `url`.

### P13: Fixtures with provenance, guard test, and the fake instance server (R8; AC35)
- **Files:** [`tests/fixtures/automation_bridge/README.md`, `tests/fixtures/automation_bridge/hello.json`, `tests/fixtures/automation_bridge/authentication.json`, `tests/fixtures/automation_bridge/get_actions.json`, `tests/fixtures/automation_bridge/do_action.json`, `tests/fixtures/automation_bridge/subscribe.json`, `tests/fixtures/automation_bridge/custom_event.json`, `tests/fixtures/automation_bridge/active_viewers.json`, `tests/fixtures/automation_bridge/chat_twitch.json`, `tests/fixtures/automation_bridge/chat_kick.json`, `tests/fixtures/automation_bridge/chat_youtube.json`, `tests/fixtures/automation_bridge/notices_twitch.json`, `tests/fixtures/automation_bridge/notices_kick.json`, `tests/fixtures/automation_bridge/notices_youtube.json`, `tests/fixtures/automation_bridge/error_response.json`, `tests/test_automation_bridge.py`]
- **Description:**
  - **Fixture format.** Each fixture file is `{"frames": [{"provenance": {"kind": "documented", "ledger_id": <int>} | {"kind": "observed", "trial_record": "docs/campaigns/phase5/bridge-trial-record.md#qN"}, "question": <1..8, optional>, "frame": {...}}]}`.
  - **Contents.** Every frame is taken from the documented schemas cited by ids in `docs/research/sources-ledger-streamerbot.json`: requests (id 2), authentication (id 4), events (id 6), and the further ids the build looks up. The frames are the hello with and without authentication, `GetActions`, the `DoAction` acknowledgement and error, `Subscribe`, the custom-event broadcast, the active-viewer list, each platform's chat message, and each notice kind the ledger documents for that platform.
  - **Section 9 questions.** No fixture asserts a behaviour that the study's section 9 lists as unverified. In particular there is no frame for the answer to a nonexistent action name (question 1), and none for a custom-event round trip (question 6). So at merge, the count of fixtures naming a question is 0.
  - **Fake server.** `tests/test_automation_bridge.py` gains a `FakeInstance` helper: an aiohttp `web` WebSocket server on `127.0.0.1` with an ephemeral port. It replays fixture frames, records every received frame (by request id), and can require authentication, refuse it, send a malformed hello, fail inventory, delay or withhold acknowledgements, close the connection, push N events, and stop.
  - **No vendor name** is used in identifiers outside `modules/streamerbot` and the fixtures.
- **Dependencies:** [P12]
- **Tests:** **AC35 guard.** Load every fixture file and fail if:
  - any frame lacks a provenance;
  - a `documented` provenance names an id absent from the ledger;
  - a frame naming a question 1–8 is not `observed`.

  Assert that the count of frames naming a question is 0 while `docs/campaigns/phase5/bridge-trial-record.md` does not exist. A self-test: the fake server answers one hello and records one frame.
- **Risks:**
  - The ledger may not document every notice kind per platform. AC43 counts only kinds the ledger documents, so the fixture set **is** the list. The step records the enumerated kinds in the fixtures README.
  - A documented schema field may be ambiguous. When it is, the fixture follows the documented example verbatim, and the ambiguity is noted in the README for the trial.

### P14: Connection lifecycle — connect, authenticate, inventory, readiness, degraded reasons, reconnect, shutdown, redaction (R1, R6; AC3, AC4, AC23 sweep, AC24, AC25, AC28)
- **Files:** [`modules/streamerbot/__init__.py`, `tests/test_automation_bridge.py`]
- **Description:** Non-trivial: a network boundary, a concurrency model and an ordering guarantee.
  - **One aiohttp client.** The connection is opened with one `aiohttp.ClientSession().ws_connect(url, timeout=connect deadline)`. It is the single connection (R6, R10), held by a connection-owner task spawned through `context.tasks.spawn`.
  - **Handshake:**
    1. Read the hello within `authentication_seconds`; a malformed hello → `protocol_error`.
    2. If the hello carries `authentication.{salt, challenge}` and no password is configured → `authentication_required`.
    3. Otherwise send `Authenticate` with `base64(sha256(base64(sha256(password+salt)) + challenge))` (AC24). An error answer → `authentication_failed`.
    4. Send `GetActions` within `request_seconds`, and build the inventory: name → list of entries, each with id, group and enabled. A failure → `inventory_unavailable`.
    5. Reconcile (P15).
    6. Send `Subscribe` (P16).
    7. Only then call `actions.mark_ready()` and `supervision.ready()`.
  - **Startup never fails (R1).** `prepare` registers the actions and returns. The connection attempt runs in `start_inputs`, which never raises for these causes: it reports `supervision.degraded(reason=<one of the 5>)`, which yields one `module.degraded` record (memory "coordinator-failure-names-module-only": the reason goes on `module.degraded`), and starts the reconnect loop.
  - **Losing the connection** → at once `actions.mark_not_ready()` and `degraded(reason="unreachable")`.
  - **Reconnect.** Bounded exponential backoff from `min_delay_seconds` to `max_delay_seconds`, using the context `clock` and an injectable sleeper (the pattern of `modules/clips/__init__.py:_pause_until`). Success → `ready()` again, never silently.
  - **Events while disconnected** are lost (documented in P21).
  - **Inbound reader.** One reader dispatches request responses to per-request-id futures and events to the bounded queue (P16).
  - **Shutdown.** `close()` sets stopping, fails every pending request future (a generic `ConnectionClosed` outcome that P15's run waiter later maps to `acknowledgement_lost`, D8), and closes the WebSocket within the existing close budget. `CancelledError` on the owner task propagates.
  - **Providers at this step.** The run action keeps P12's placeholder provider; it is never reached while the bridge is not ready, because the executor refuses `provider_not_ready` first. `users.read` is not registered by the bridge until P18.
  - **Credentials.** The password is a declared credential and is redacted by the loader. The module never logs the URL, the password or frame text: diagnostics carry reasons only.
- **Dependencies:** [P13]
- **Tests:** Against `FakeInstance`:
  - **AC3 (run-action half).** A loopback port where nothing listens; use a full `run` with a tmp profile enabling the bridge and a granted `automation.run` rule. The companion reaches readiness within its startup budget, the bridge has exactly 1 `module.degraded` with `unreachable`, and a run call ends `refused provider_not_ready` with 0 frames (the executor's not-ready rule, no P15 code needed). Assert the outcome rather than exit status 0 (memory "coordinator-drain-hook-tie"). The test is written as a parametrised helper so P18 adds the `users.read` half (finding P3).
  - **AC4.** The four failure modes give the four reasons, and a trace/log sweep finds the password, the URL and frame text 0 times.
  - **AC24.** The exact auth response for known salt, challenge and password; ready only after the inventory is read.
  - **AC25.** Closing the socket while ready → 1 `module.degraded` at once. After advancing the injected clock by `min_delay` → 1 `module.ready`. The recorded delays never exceed `max_delay`.
  - **AC28 (connection half).** Cancelling the owner task raises `CancelledError`. Shutdown makes the fake server observe a close within budget, and a pending `GetActions` request future is failed, not left hanging. The in-flight run clause moves to P15 (finding P3).
  - **AC23 sweep (connection scenarios).** Over this step's scenarios the configured password appears 0 times in all traces. P15 re-runs the sweep over its run scenarios.
- **Risks:**
  - **Hung handshake.** Each step is bounded by its own deadline, and the whole start by the coordinator's startup budget.
  - **Error answers.** The exact error frame shape for a refused authentication must be taken from the documented fixture; the trial (Q3) confirms it.
  - **Reconnect storms.** The backoff is capped and jitter-free, so tests stay deterministic.

### P15: Allowlist, reconciliation and the run-action provider with the full outcome table (R4, R5; AC16–AC21, AC45)
- **Files:** [`modules/streamerbot/__init__.py`, `tests/test_automation_bridge.py`]
- **Description:** Non-trivial: branching outcomes and an "at most one frame" invariant.
  - **Reconciliation** runs at each successful connection:
    - The reachable set is the configured `action:` names, plus every instance action of a configured `group:`, that exist exactly once and are enabled.
    - It emits exactly one value-free diagnostic trace, `automation.allowlist.reconciled`, that lists unmatched entries by configured name.
  - **Run provider.** Decisions happen strictly before any frame, in this order:
    1. Not in the allowlist → `refused not_allowlisted`. An empty or absent allowlist refuses everything.
    2. Absent from the inventory → `refused instance_action_missing`.
    3. A name with 2+ instance actions → `refused instance_action_ambiguous`.
    4. Disabled → `refused instance_action_disabled`.
    5. An argument violation → `error invalid_arguments`.
       - The deciding schema is the action's `action:` entry alone if one names it.
       - Otherwise the action must satisfy **every** matching `group:` entry's schema.
       - An entry without a schema accepts only `{}`.
       - Validation uses `validate_against_schema`.
  - **Not emitted until the boundary (D8).** The provider's first statement, before any `await`, is `invocation.mark_not_emitted()`.
  - **Sending:**
    - Serialise one `DoAction` frame naming the **instance action id** from the inventory. This never relies on the instance's handling of a nonexistent name (R8, question 1).
    - Then the transmission segment of D8, with no `await` before the transport write: the pre-write check (bridge `connected` flag, `ws.closed`, protocol transport present and not `is_closing()`). If it fails → return `refused provider_not_ready`, 0 frames, emission stays `not_emitted`. Otherwise `invocation.mark_emitted()`, set the per-call `sent` latch, and `await ws.send_str(frame)`. The write happens before that coroutine's first suspension (D8). Any exception or `CancelledError` from that await is post-write → `external_unknown`, cause `acknowledgement_lost`.
    - Before the segment, the provider awaits an injectable `_before_transmit` hook (default: returns at once). It is a test seam that makes the pre-send interval observable, and it sits strictly before `mark_emitted()`.
    - Await the response future up to `acknowledgement_seconds`.
    - An acknowledgement → `external_unknown`, code `external_effect_unknown`, cause `execution_unconfirmed`.
    - An error answer → `error provider_failed`, cause `instance_rejected`.
    - No acknowledgement before the deadline, or the connection lost after sending → `external_unknown`, cause `acknowledgement_lost`.
    - `CancelledError` after sending → the provider-authored interruption record with cause `acknowledgement_lost`, per D8 and the clips precedent.
    - Every non-success has `retryable: false`, and the uncertain messages state that "the companion does not know whether the action ran".
  - **Invariants.** No retry, never `success`, and at most one frame per call (a per-call `sent` latch).
  - **Not-ready** is left to the executor (`provider_not_ready`). A pre-send deadline or cancellation is left to the executor's emission rule, which yields `timeout`/`cancelled` because the provider marked `not_emitted` and the segment has not run.
  - **In-flight runs at shutdown.** The run waiter maps P14's failed request future (`ConnectionClosed`) to `external_unknown`, cause `acknowledgement_lost`.
- **Dependencies:** [P14]
- **Tests:** Against `FakeInstance`, one test per R4 row (**AC16**, 10 tests). Each asserts status, code and cause exactly, `retryable: false`, and the fake server's request-frame count: 0 for the first 6 rows and for "before the frame left", 1 otherwise. A suite-level assertion: no run observation has `status: success`.
  - **AC17.** A late acknowledgement gives `acknowledgement_lost`, `executor.provider_invocations` unchanged after the result, and exactly 1 frame for the `call_id`.
  - **AC18.** A run of `B` against `allowlist: [{action: "A"}]` → `not_allowlisted` with 0 frames. An empty allowlist refuses everything. With a group entry, `A` is reachable and `B` is refused.
  - **AC19.** The argument schema cases.
  - **AC45.** The action entry beats the group entry, so `A {n:1}` sends 1 frame. Two group entries must both accept: `invalid_arguments` with 0 frames, and an argument set both schemas accept sends 1 frame.
  - **AC20.** Exactly 1 reconciliation diagnostic naming `C`, `D` and `E` and not `A`; runs of `C`/`D`/`E` give `ambiguous`/`disabled`/`missing` with 0 frames, and `A` sends 1 frame.
  - **AC21.** A principal with no `actions:` rule → `refused not_authorized`, with 0 provider invocations.
  - **Transmission boundary (finding P6).** With `_before_transmit` blocked on an event:
    - cancelling the call → `cancelled`, existing code, 0 frames at the fake server;
    - letting the executor's deadline expire → `timeout`, existing code, 0 frames;
    - closing the fake connection, then releasing the hook → `refused provider_not_ready`, 0 frames.
    - Cancelling **after** the segment (acknowledgement withheld) → `external_unknown`, cause `acknowledgement_lost`, exactly 1 frame.
    - **Pre-write failure (finding P6, round 2).** With `_before_transmit` blocked, make the client's transport closing without a loop turn reaching the bridge (the test closes the protocol transport, or patches its `is_closing` to return `True`), then release the hook → `refused provider_not_ready`, `invocation.emission == not_emitted`, 0 frames at the fake server, and the bridge reports not-ready.
    - **Post-write interruption.** Pause the client protocol's writing (`protocol.pause_writing()` with the writer's output size pushed over its limit), so the send suspends in `_drain_helper` after the write. Cancelling the call there → `external_unknown`, cause `acknowledgement_lost`, emission `emitted`, exactly 1 frame once the protocol resumes. Expiring the executor's deadline there gives the same outcome.
    - A static check of the provider's source (AST) asserts no `await` between the pre-write check, the `mark_emitted()` call and the `ws.send_str` call, and that `mark_emitted()` comes after the pre-write check.
    - **aiohttp canary (D8).** The `ast` test of the installed `WebSocketWriter.send_frame`: no `Await` before `_write_websocket_frame` in the uncompressed branch, and only the two closing-check raises before the write. Plus a test that the bridge's `ws_connect` call passes `compress=0`.
  - **AC28 (in-flight half, moved from P14).** A run whose frame was sent and whose acknowledgement is withheld, then bridge shutdown → `external_unknown`, cause `acknowledgement_lost`, 1 frame.
  - **AC23 sweep (run scenarios).** The configured password appears 0 times in all traces.
- **Risks:**
  - **Stale inventory.** An action renamed after connection → the instance's error answer → `instance_rejected`. The guide documents the reconnection-time reconciliation.
  - The executor's own timeout must stay larger than the acknowledgement deadline (P12 validator), or the executor's generic record pre-empts the cause.
  - **Client internals.** D8's "no await before the transport write" was read in the installed aiohttp 3.14.3. The canary test fails loudly if an upgrade changes it (`pyproject.toml` allows `>=3.9,<4`). The fallback is the module-local synchronous writer of D8. Record the route taken in the commit message.
  - **Pre-write check drift.** If aiohttp adds a new pre-write raise, the canary's "only the closing checks" assertion fails before the boundary can silently misreport.

### P16: Subscriptions, bounded inbound queue and cue mapping through the trigger engine (R6, R7a; AC26, AC27, AC29, AC30, AC33 engine half)
- **Files:** [`modules/streamerbot/__init__.py`, `tests/test_automation_bridge.py`]
- **Description:** Non-trivial: concurrency, a bounded queue, and a module-to-engine boundary.
  - **Subscribe** names only the categories that the owned pairs need:
    - `cues` owned on any platform → the custom-event category only;
    - each owned (`chat`, P) → P's chat-message and notice categories only;
    - viewers needs no subscription (pulled on demand).
  - **Bounded queue.** The reader pushes events into an `asyncio.Queue(maxsize=queue_max_events)` with `put_nowait`. On `QueueFull` it drops the event and counts it in `dropped_events`, a module counter exposed in the readiness details, and never blocks the reader, so request acknowledgements keep flowing.
  - A consumer task drains the queue.
  - **Cue envelope.** It is validated strictly:
    - `cue_id`: a non-empty string of at most 128 characters;
    - `platform`: non-empty;
    - optional `channel_id`, defaulting to `instance_identity.<platform>`; a missing default drops the cue;
    - optional `user_id` with `user_name`;
    - `text`: a string of at most 500 code points.

    A malformed or oversized envelope is dropped and counted, never truncated.
  - **Normalised event.** When the bridge owns (`cues`, platform), it builds a schema-version-2 `channel.chat.message` with `payload.kind: cue`, `message_id = cue_id`, and `metadata.source = MODULE_NAME`. The author is the envelope user, or the stable `instance:<platform>` identity. The `:` makes the identity distinct from every platform account id, since every platform module refuses `:` (see `modules/watch/__init__.py:43`), and so distinct from configured companion identities.
  - **Evaluation and publication.** The bridge evaluates the event with `context.triggers.evaluate(event, context=TriggerContext(()))` under `triggers.<module>`, then publishes through `context.bus` (guarded by P4), mirroring the twitch sequence at `modules/twitch/__init__.py:2165-2185`.
  - **Anti-echo.** At activation, `context.triggers.add_companion_identities(companion_identities.values())` (D6).
- **Dependencies:** [P15]
- **Tests:**
  - **AC26.** A bound of 4 and 10 events pushed before the consumer runs: 4 published, `dropped_events == 6`, and a request sent afterwards is acknowledged.
  - **AC27.** Owning only (`cues`,`*`) → the subscribe frame names only the custom category. Adding (`chat`, twitch) adds only twitch chat and notices.
  - **AC29.** One event with the exact fields. The trigger engine evaluates it with the `triggers.<bridge>` policy, and the default policy accepts it.
  - **AC30.** The same default author across 2 cues, distinct from the companion identities. 501 code points, an empty `cue_id`, or a missing `platform` → 0 events and +1 dropped each.
  - **AC33 (engine half).** With the identity set, a mapped instance chat event authored by it is rejected by the engine's anti-echo reason; this needs P17's chat mapping.
- **Risks:**
  - **Wildcard platform.** A cue for a platform the bridge does not own is dropped by the bridge itself, before the guard counts it, and counted as dropped.
  - **Replays.** A `cue_id` reused by the instance falls in the engine's dedup window; document it.

### P17: Chat mapping for owned platforms and role trust (R7b; AC32, AC33, AC43)
- **Files:** [`modules/streamerbot/__init__.py`, `tests/test_automation_bridge.py`]
- **Description:** Non-trivial: a per-platform mapping that must equal the native shape.
  - **Mapping.** For each owned (`chat`, P), map P's documented chat-message event and each documented notice kind to a normalised event. The payload key set and `kind` must equal what P's native module produces: `platform`, `channel_id`, `author{id, display_name[, roles, roles_provenance]}`, `message_id`, `text`, and `kind` only for notices, per `modules/twitch/__init__.py:401-430`. Kick and youtube are checked the same way.
  - **Unmapped kinds.** An instance event with no documented mapping → dropped and counted.
  - **Roles** are emitted only when `trust_instance_roles` is true, with `roles_provenance` naming the bridge's module. Otherwise no `roles` key is emitted and `TriggerContext(())` is used, so a moderator audience rule is not satisfied.
  - **Anti-echo.** The events go through the same evaluate → publish path as P16, so the engine's anti-echo applies.
  - The mapping table is a module-local dict keyed by (platform, documented event name). Platform names appear only in `modules/streamerbot`.
- **Dependencies:** [P16]
- **Tests:**
  - **AC43.** For each P in {twitch, kick, youtube}, owning (`chat`, P): the chat fixture and every documented notice fixture each give exactly 1 event whose kind and key set equal the native module's for the equivalent input. Native shapes are built inline in the test by calling each native module's own normaliser on its existing test payloads (e.g. the twitch notification `event()` at `modules/twitch/__init__.py:401`), not by stored fixtures, so P13's provenance guard stays about instance frames. An undocumented notice gives 0 events and +1 dropped. Owning only twitch, the kick and youtube fixtures give 0 events.
  - **AC32.** Key and kind equality for twitch. With `trust_instance_roles` false, an `audience: moderators` rule is unmet by the fixture's moderator flag; with `true`, it is met.
  - **AC33.** `--check-config` on a profile where the bridge owns (`chat`, twitch) with no identity fails naming `twitch`. With the identity set, an event authored by it is rejected by the engine.
- **Risks:** Native payload shapes can drift. The test derives them from the native code, not from a copy. A notice whose required fields the instance does not document is not mapped, by rule.

### P18: Bridge `users.read` from the instance's active viewers (R7c, R3; AC34, AC14, AC3 users.read)
- **Files:** [`modules/streamerbot/__init__.py`, `tests/test_automation_bridge.py`, `tests/test_input_ownership.py`]
- **Description:** Non-trivial: a shared action spec across modules.
  - **Registration.** At `prepare`, register `users.read` with the spec parsed from `modules/users/module.yaml`, the same parser as `_declared_users_read_spec`, so the specs are byte-equal. The P5 facade binds it only for owned (`viewers`, P).
  - **Serving.** For a destination channel equal to `instance_identity.<P>`, request the instance's active-viewer list for P within `request_seconds`. Answer one page with:
    - at most `limit` users;
    - `first_seen`/`last_seen`/freshness fields as the shared schema defines;
    - `coverage.kind: active_viewers`, `coverage.complete: false`.

    Any other channel → `refused unsupported_destination`. A connection that is not ready is refused `provider_not_ready` by the executor.
  - **Nature.** `users.read` is a `read`, so a timeout keeps `timeout` (executor rule).
- **Dependencies:** [P17]
- **Tests:**
  - **AC34.** The page validates against the shared result schema, with `active_viewers` and `complete: false`, and has at most `limit` users. Another channel → `unsupported_destination`. The `users` pages still report `observed_authors`.
  - **AC14 (real modules, in `tests/test_input_ownership.py`).** `users` plus the bridge, with `inputs: {viewers: {twitch: <bridge>}}`: preparation succeeds, a twitch read reaches the bridge, and a kick read reaches users (1 invocation each).
  - **AC3 (`users.read` half, moved from P14, finding P3).** Extend P14's parametrised AC3 test: with the bridge unreachable and `inputs: {viewers: {twitch: <bridge>}}`, a `users.read` at the bridge's twitch destination ends `refused provider_not_ready` with 0 frames.
- **Risks:**
  - The instance's active-viewer list may lack per-viewer timestamps. Use the fetch instant for `last_seen`/`first_seen` and state it in `freshness`, as documented.
  - `limit` and `cursor` semantics: the cursor pages over one fetched snapshot; a cursor from another destination is refused as by `users`.

### P19: R1 no-change guarantees and the core vendor-name guard (R1, R3, R10; AC1, AC2, AC5, AC11, AC44 shipped profiles)
- **Files:** [`tests/test_input_ownership.py`]
- **Description:** Cross-cutting regression tests, added once every module carries its declaration:
  - **AC1 and AC2.** For each of the 4 shipped profiles, with the test `${NAME}` values of `tests/test_examples.py`:
    - `--check-config` exits 0, with no ownership diagnostic and the same diagnostic output as the baseline captured at P1 by running the same command on the pre-phase commit (stored in the test as the expected list);
    - none of the files contains the bridge module name or an `inputs:` key;
    - `git diff --stat <phase base>` reports 0 changed lines in them (skipped with a reason when git is unavailable).
  - **AC44 (shipped half).** For each shipped profile, resolve ownership and assert that no undeclared shipped module's publishable pair has an owner it does not hold. Run `tests/test_integration.py` scenarios with a counter probe: `input_ownership_rejections == 0`.
  - **AC5 (end to end).** The bridge owns (`chat`, twitch), the fake instance is stopped, and a twitch event published by the twitch module is dropped (P8's driver). The bridge's schema has no `required`.
  - **AC11.** A case-insensitive grep over `core/` for the bridge module name and the vendor name returns 0. The ownership functions in `core/loader.py` and `core/runtime.py` contain no platform-name literal from the set {twitch, kick, youtube} (AST string-constant scan).
- **Dependencies:** [P18]
- **Tests:** As described. Also run the full suite: only the 7 allowlisted tests may have needed adaptation, plus any recorded in P1/P7.
- **Risks:**
  - **Baseline capture.** If the baseline output is not empty, it must come from the pre-phase commit (`f900f27`), not from the edited tree.
  - The grep must exclude `__pycache__`.

### P20: Opt-in real-instance trial nodes and the "not delivered" docs guard (R8; AC36, AC37)
- **Files:** [`tests/test_phase5_trials.py`, `tests/test_phase5_docs_status.py`]
- **Description:**
  - **The 8 nodes.** One `pytest` node per study question:
    1. nonexistent-name behaviour;
    2. latency and blocking queue;
    3. enforce-mode authentication;
    4. minimum version per request;
    5. C# HTTP/JSON availability;
    6. custom-event round trip;
    7. servers under Wine;
    8. subscribed-event volume.
  - **Skipping.** Each is skipped unless its variables are set, for example `PHASE5_TRIAL_URL`, `PHASE5_TRIAL_PASSWORD` and per-node variables. The skip reason names them.
  - **Nodes 5 and 7** record an operator observation passed through variables, rather than probing.
  - **Sockets.** No socket is opened when skipped: the import is lazy, and the skip is decided in the body before any I/O.
  - Mirror the structure of `tests/test_phase3_trials.py`.
  - **Docs guard (AC37), in its own file `tests/test_phase5_docs_status.py` (finding P5).** It is not in `tests/test_phase5_trials.py`, so that file collects exactly the 8 trial nodes (AC36), and the docs guard is never skipped with them. While `docs/campaigns/phase5/bridge-trial-record.md` is absent:
    - `README.md` and `docs/README.md` must contain the bridge's status "not delivered";
    - no tracked Markdown file applies "delivered" to the bridge outside those two "not delivered" statements (a regex over lines that mention the bridge or its guide).

    Once the record exists: it must carry an instance version, a date and one answer for each question 1–8.
- **Dependencies:** [P15]
- **Tests:**
  - **AC36.** `pytest tests/test_phase5_trials.py` with no variable → 8 collected, 8 skipped, each reason naming its variables, and 0 sockets (socket-creation spy).
  - **AC37.** `tests/test_phase5_docs_status.py` runs unconditionally (no trial variable). It fails until P21 adds the README rows, so P21's tests must pass together with it, and P22 runs both files.
- **Risks:**
  - The AC37 regex could flag unrelated uses of "delivered" (e.g. "delivery"). Match the whole word, and only on lines naming the bridge or its guide.
  - Order: P20's docs guard is red until P21. To keep each step green, P20 marks the AC37 test in `tests/test_phase5_docs_status.py` `xfail(strict=True)` with the reason "guide rows land in P21", and P21 removes the marker. This is flagged so it is not read as a relaxed test.

### P21: Operator guide (EN/FR), design addendum (EN/FR), indexes, bilingual registration, combination tests (R8, R9, R10, D3, D5, D6; AC37, AC39, AC40, AC42)
- **Files:** [`docs/automation-bridge.md`, `docs/automation-bridge.fr.md`, `docs/design-v2.md`, `docs/design-v2.fr.md`, `docs/README.md`, `README.md`, `tests/test_docs_bilingual.py`, `tests/test_automation_bridge.py`, `tests/test_phase5_docs_status.py`]
- **Description:**
  - **The guide, `docs/automation-bridge.md`** (marked normative). It covers:
    - what the bridge does and does not do;
    - every setting;
    - the allowlist, including a statement that a group entry delegates the choice of reachable actions to whoever edits that group in the instance;
    - ownership and no fallback;
    - the R4 outcome table, worded so that it never claims execution;
    - events lost while disconnected;
    - the cue recipe;
    - the trust model (A5, and P11's risk);
    - the trial status;
    - the **combination table, 12 rows**, each with non-empty "enabled", "disabled" and "refused" cells. Row 8 states that families and cues do not reach the brain in the agent-process placement.
  - **Profile fragments.** Each row has one fenced YAML fragment, marked with `<!-- combination N base=<profile> -->`, so the test can extract it.
  - **`docs/automation-bridge.fr.md`.** A French translation with the same sections and fenced blocks, every backticked identifier, and the statement that the English text wins.
  - **The design addendum, `docs/design-v2.md`** (and its translation). It covers the input-ownership rule: the vocabulary, D1, D2 resolution, the runtime guard (D3/D4, including the guarded v1 route), gated binding (D5), engine identities (D6) and the ownership-aware hook (D7).
  - **`docs/README.md`.** List both guide files, and add the phase-5 trial row with status "not delivered — awaiting real-instance trial".
  - **`README.md`.** Add the bridge to the module list with the same status.
  - **`tests/test_docs_bilingual.py`.** `GUIDES` gets `"automation-bridge"`.
  - **`tests/test_phase5_docs_status.py`.** Remove the P20 `xfail` marker. `tests/test_phase5_trials.py` is not touched.
- **Dependencies:** [P19, P20]
- **Tests:** In `tests/test_automation_bridge.py`:
  - **AC39.** Extract the 12 fragments, apply each to a tmp copy of the stated shipped profile, set the test `${NAME}` values, and run `--check-config`. Rows 1–8 exit 0. Rows 9–12 exit non-zero with the documented diagnostic substring, all under a socket-creation spy (0 transports).
  - **AC40.** The table has 12 rows, no cell is empty, and row 8 contains the sentence.
  - **AC42.** `tests/test_docs_bilingual.py` passes with the pair. The English file marks itself normative, and the French file states that the English text wins.
  - **AC37** (`tests/test_phase5_docs_status.py`) now passes unmarked, and `tests/test_phase5_trials.py` still collects exactly 8.
- **Risks:**
  - **Row 7** (remote brain, `wss://`) must check without a reachable host. `--check-config` opens nothing, so it passes.
  - **Row 8** uses `agent.yaml.example` and relies on the proxy's actions allowlist finding the run action in the discovered catalog. The test must assert this through check-config, not a live proxy.
  - The bilingual check requires every backticked token of the English text to appear in the French one. Write the French text last, from the final English text.

### P22: Full-branch review gate and tracked report (R8; AC38; all of R1–R10)
- **Files:** [`docs/campaigns/phase5/full-branch-review.md`]
- **Description:** Before delivery, review the **whole branch diff** (`git diff main...HEAD`) as one unit, not commit by commit.
  - **Why the per-commit reviews are not enough.** Each step's review sees one commit, so a cross-file defect is invisible by construction: each commit can be correct alone while the set is broken where files interact. Component decomposition is not integration. Examples here:
    - P4's guard and P8/P9's ingestion gates must agree on the "no ownership table = unrestricted" default;
    - P5's exclusions and P18's bridge binding must never overlap;
    - P4's v1 facade and the loader's v1 route must agree on `constrains_any()`;
    - P14's failed request futures and P15's run waiter must agree on the `acknowledgement_lost` mapping;
    - P12's `timeout_seconds` and P15's acknowledgement deadline must keep the cause intact;
    - P11's cue trust and P16's author identity must not let a viewer forge a cue;
    - D1's family set and the `watch` module's evaluations must stay compatible.
  - **The checklist:**
    - every R1–R10 and AC1–AC45 is mapped to a passing test;
    - the full suite is green except the allowlisted tests, each adapted as listed; record any test added to the allowlist in P1/P7 with its contradicting requirement;
    - re-run the AC11 grep;
    - grep `modules/` for any use of `excluded_platforms` or `add_companion_identities` outside the intended callers;
    - the deviations D5 and D6 (7 core files instead of 5) are stated;
    - P11's consumer audit result is recorded and decided;
    - the R6 "no default endpoint, port, password, version or action name" grep is run over `modules/streamerbot` and the tests;
    - the 4 shipped profiles have 0 changed lines;
    - AC38: the report is tracked.
  - **The report** is written to `docs/campaigns/phase5/full-branch-review.md`, with the findings, their resolution, and the commands run with their results.
- **Dependencies:** [P21]
- **Tests:** The full suite (`pytest -q`), `pytest tests/test_phase5_trials.py -q` (8 collected, 8 skipped) and `pytest tests/test_phase5_docs_status.py -q` (passing). Record the three summary lines in the report.
- **Risks:**
  - Gate findings may require fixes in earlier files. They are applied as fix commits before the report is finalised, and the report lists them.
  - The phase is **not** "delivered" at merge. Only the trial record (outside this plan) changes that.

## Ordering rationale

- **P1 comes first** because every later step needs the vocabulary: the family names, `cue`, the counter and the declaration parser. It depends on nothing.
- **P2 → P3 → P4** build the ownership rule bottom-up:
  - P2's resolver is pure and testable alone.
  - P3 wires it into `--check-config`, so refusals are observable without transports before any runtime behaviour exists.
  - P4 adds the runtime guard, which needs both the resolved table (P2) and the loader wiring it extends.
- **P5 and P6 follow P4** because they extend P4's facades: the gated `ModuleActions` binding, and the delegated engine method. P6 depends on P5 explicitly because both edit `tests/test_input_ownership.py`.
- **P7 (UI) needs P3**, since Check goes through `check_config`.
- **P8 → P9 → P10** declare the shipped modules' families one module group at a time, chained because they all edit `tests/test_examples.py`. P10 also needs P5's gated binding.
- **P11 needs only P1** (`cue` and `PLATFORM_NOTICE_KINDS`). It is placed before the bridge so AC31 can use the run action's delivery shape.
- **The bridge is built upward**, chained because every step edits `modules/streamerbot/__init__.py` and `tests/test_automation_bridge.py`:
  - P12 (manifest, validator, packaging) needs P2 (key), P6 (engine method) and P10 (the shared `users.read` spec);
  - then P13 (fixtures and fake server);
  - then P14 (connection, which every later behaviour runs over; its tests assert only what P14 implements);
  - then P15 (run action, which needs reconciliation at connect; it carries AC28's in-flight clause);
  - then P16 (queue and cues);
  - then P17 (chat mapping reuses P16's pipeline);
  - then P18 (viewers, the last provider; it also closes AC14 with the real modules and AC3's `users.read` clause).
- **P19** adds the cross-cutting R1 regressions once every shipped module and the bridge exist, because AC1, AC2 and AC44 compare the final tree.
- **P20** needs P15, so that a trial node can drive a real run action. It is placed before P21 so the AC37 guard exists before the README rows it checks. The interim `xfail(strict=True)` is explicit and removed in P21.
- **P21** documents the final behaviour and runs the fragments through `--check-config`, after every refusal (P2, P3, P12, P17) exists.
- **P22 is the full-branch gate**, last by construction.
- **Shared-file chains, so a sequential dev loop never interleaves edits:**
  - `core/loader.py`: P2 → P4.
  - `core/runtime.py`: P4 → P5.
  - `tests/test_input_ownership.py`: P1 → P2 → P3 → P4 → P5 → P6 → P18 → P19.
  - `tests/test_examples.py`: P8 → P9 → P10 → P12.
  - `modules/streamerbot/__init__.py` and `tests/test_automation_bridge.py`: P12 → P13 → P14 → P15 → P16 → P17 → P18 → P21.
  - `tests/test_phase5_trials.py`: P20 only.
  - `tests/test_phase5_docs_status.py`: P20 → P21.

## Requirement coverage

| Requirement | Steps |
|---|---|
| R1 | P3, P8, P12, P14, P19 |
| R2 | P1, P2, P3, P7, P8, P9, P10 |
| R3 | P1, P4, P5, P8, P9, P10, P18, P19 |
| R4 | P12, P15 |
| R5 | P12, P15 |
| R6 | P12, P14, P16 |
| R7 | P6, P10, P11, P16, P17, P18 |
| R8 | P13, P20, P21, P22 |
| R9 | P21 |
| R10 | P1, P12, P19, P21 |
