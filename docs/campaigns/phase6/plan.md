---
spec: "phase6-adversarial-review-fixes"
version: "1.0"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: true
---

# Implementation Plan

Authority: `docs/reviews/2026-10-16-full-adversarial-review.md` (findings `F1`–`F35`), the spec
`spec.md` (R1–R10, AC1–AC39) and the brief `docs/campaigns/phase6/brief.md`. Pre-fix tree for
AC37 evidence: `8010edc`. Every product-defect step records, in its commit message, the run of its
new regression test against `8010edc` (fails) and at the step head (passes); every test-defect step
(F27, F28, F32) records the mutation, the failing run under it and the passing run after restore.
No step adds a `skip`/`xfail`, deletes or loosens an assertion (AC38); the only assertion
replacements are the allowlisted tests, each carrying a docstring citing its finding and
requirement. No real platform, Streamer.bot, OBS, model service or audio device is contacted.

Finding → step map: F1 P2 · F2 P3 · F3 P1 · F4 P5 · F5 P3 · F6 P7 · F7 P1 · F8 P8 · F9 P9 ·
F10 P9 · F11 P10 · F12 P11 · F13 P4 · F14 P3 · F15 P18 · F16 P18 · F17 P18 · F18 P18 · F19 P4 ·
F20 P11 · F21 P14 · F22 P14 · F23 P12 · F24 P9 · F25 P13 · F26 P6 · F27 P19 · F28 P19 · F29 P1 ·
F30 P16 · F31 P14 · F32 P15 · F33 P15 · F35 P17 · F34 and `claude:F8` out of scope, dispositions
recorded in P20.

## Steps

### P1: Executor replay protection binds the whole call; concurrent duplicates coalesce; finite grace (R1; F3, F7, F29; AC3, AC4, AC5)
- **Files:** [`core/actions.py`, `tests/test_actions.py`]
- **Description:** In `ActionExecutor` (`core/actions.py`), replace the `call_id → observation`
  retention (`self._outcomes`, read at `invoke` ~line 1197) with a retention keyed by `call_id`
  whose entry also stores the call's **logical identity**: principal, session, action, destination
  and a canonical (sorted-key JSON) form of the arguments. On a reused `call_id`, `invoke` first runs
  authorization for the *new* call exactly as for a fresh one (default-deny unchanged); only when it
  is authorized **and** its identity equals the retained identity is the retained observation
  returned. A reuse with any differing identity component, or one the policy refuses, returns a new
  refusal observation (new terminal status, e.g. `call_id_conflict` / the policy refusal) that copies
  none of the retained `result` fields, and never invokes the provider (F3). Add an in-flight table
  `call_id → (identity, asyncio.Future[ActionObservation])` populated before the provider is awaited:
  a second identical invocation awaits the same future (both callers get the same terminal
  observation, provider and handler run once); a non-identical concurrent reuse is refused as above
  (F7). The future is resolved on every terminal path (success, failure, cancellation, timeout) and
  removed in a `finally`, so a cancelled first caller still settles waiters. `outcome(call_id)` keeps
  its signature. In the constructor, add `math.isfinite` to the `cancel_grace_seconds` check
  (~line 1082–1088): NaN/±inf raise `ContractError("ActionExecutor.cancel_grace_seconds", "must be a
  finite number")` (F29). Update the module docstring paragraph (line ~77) that states the old
  call_id-only rule.
- **Dependencies:** []
- **Tests:** In `tests/test_actions.py`: AC3 — parametrized over the 5 differing components
  (principal, session, action, destination, arguments): after `c1` by `brain` completes, the reuse is
  refused, provider count unchanged, and the observation contains none of the original result keys;
  plus a case where the identical reuse is refused by a policy change (policy refuses → refused even
  if identical). AC4 — two `invoke()` of the same logical call started with `asyncio.gather` against
  a fake provider gated on an `asyncio.Event` so both await before completion: provider count == 1,
  handler run count == 1, both observations have equal status and result; plus the first caller
  cancelled while the second waits still yields a terminal observation. AC5 — `nan`, `inf`, `-inf`
  raise `ContractError` naming `cancel_grace_seconds`; `0` and `0.5` accepted. Each test is run
  against `8010edc` (fails) for AC37.
- **Risks:** Non-trivial (branching, ordering invariant). Canonicalizing arguments must be
  deterministic and must not raise on JSON-shaped values the contract already accepts; reuse the
  argument validation's JSON form. Authorizing before the cache lookup changes the trace sequence for
  replays — existing tests asserting replay traces may need their expectations checked (assertions
  must not be loosened; if one encodes the defect it is not on the allowlist, so stop and flag). The
  in-flight future must not leak on cancellation (use `finally`), and the retention eviction bound
  (line ~170) still applies.

**Caller table (`ActionExecutor.invoke` semantics change, R1):**

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/brain/__init__.py:~3879 | brain run → `ActionExecutor.invoke` | No change: fresh `call_id` per call; a reused id now needs the same logical call. |
| modules/agent_link/__init__.py:~1154–1186 | `_on_call` received call → `invoke` | P3 passes the pairing-fixed principal; duplicate waiting now partly handled by the executor's in-flight table. |
| modules/twitch/__init__.py:~1476 | `handle_chat_send` compatibility route | P2 turns it into an `invoke` of `chat.write`. |
| core/actions.py | `ActionExecutor.outcome` | Signature unchanged; returns the retained observation. |
| tests/test_actions.py, tests/test_agentic_loop.py, tests/test_brain.py, tests/test_proxy.py, tests/test_automation_bridge.py, tests/test_delivery.py | direct `ActionExecutor(...)`/`invoke` users | Run unchanged; any that reuse a `call_id` with different content are surfaced in the step evidence. |
| (unknown) | dynamic dispatch via bus wildcard subscriptions | Not detectable by grep (spec blind spot); covered by the full suite run and the P21 gate. |

### P2: Twitch `channel.chat.send` compatibility route goes through the executor under default-deny (R1; F1; AC1, AC2; allowlisted twitch tests)
- **Files:** [`modules/twitch/__init__.py`, `modules/twitch/module.yaml`, `tests/test_twitch.py`]
- **Description:** Rewrite `handle_chat_send` (`modules/twitch/__init__.py:1476`, subscription on
  `_CHAT_SEND_EVENT` line 186) so it no longer calls the Helix transport directly: it builds an
  `ActionCall` for `chat.write` with an explicit compatibility principal (a named module constant,
  documented in the module docstring and in `module.yaml` comments) and the Twitch destination of the
  configured broadcaster channel, a fresh `call_id`, and invokes the runtime's executor (resolved from
  the module context as other modules do). With no applicable rule the executor refuses, 0 Helix
  POSTs are emitted, and the returned legacy metadata is `delivery_status: "failed"`,
  `delivery_outcome: <executor terminal status>`; a success maps to `sent`, other terminal statuses to
  `failed` with that status. When the runtime has no executor, the route refuses (default-deny), it
  never falls back to the direct transport. This makes `docs/design-v2.md:584` true. Adapt the six
  allowlisted tests in `tests/test_twitch.py` (`test_chat_send_uses_configured_identity_and_preserves_event`,
  `test_send_failure_continues_chain_and_next_send_succeeds`,
  `test_pending_send_releases_response_on_timeout_cancellation_and_close`,
  `test_notification_mapping_dedup_and_retry_reconnect`,
  `test_lost_sent_trace_neither_retries_nor_repeats_the_send`,
  `test_confirmed_outcome_never_waits_for_the_sent_trace`) by a fixture that configures the
  `chat.write` grant to the compatibility principal, assertions unchanged, each docstring citing F1
  and R1 (AC38). No shipped `*.example` profile changes (none publishes `channel.chat.send`).
- **Dependencies:** [P1] (identity-bound retention and in-flight semantics the route relies on)
- **Tests:** New in `tests/test_twitch.py`: AC1 — with an executor and no rule, publishing
  `channel.chat.send {"text": "hi"}` → 0 POSTs on the fake Helix, provider invoke count 0,
  `delivery_status == "failed"` and `delivery_outcome` equal to the executor's refusal status; with a
  matching rule → exactly 1 POST and `sent`. AC2 — exactly 1 `action.requested` and 1
  `action.completed` trace carrying the compatibility principal and Twitch destination, equal in
  shape to a direct executor call's traces. Run against `8010edc` (fails). Full `tests/test_twitch.py`
  plus `tests/test_integration.py`, `tests/test_examples.py`, `tests/test_users.py` pass.
- **Risks:** Non-trivial (module/service boundary). Any other fixture/test that sends to Twitch via
  `channel.chat.send` without a grant (caller table rows for `tests/fixtures/modules/fakeplatform`,
  `test_integration.py`, `test_shutdown.py`, `test_examples.py`) would turn red but is **not** on the
  allowlist: if one breaks, stop and flag rather than edit its assertions. The chain-continuation and
  timeout-release semantics of the old route must be preserved through the executor's timeout/cancel
  path. Recursion risk: `chat.write` provider must not republish `channel.chat.send`.

### P3: agent_link — negotiated action set, pairing-fixed principal, no peer text in diagnostics, bounded executions and waiters (R2; F2, F5, F14; AC6–AC9)
- **Files:** [`modules/agent_link/__init__.py`, `modules/agent_link/module.yaml`, `tests/test_proxy.py`]
- **Description:** In `_on_call` (`modules/agent_link/__init__.py:1046`): (a) refuse — with an
  observable terminal refusal frame and 0 executor invocations — any call whose action is not in the
  session's negotiated set (intersection of offered and configured, computed once at pairing and
  stored on `_Session`) (F2); (b) stop reading `frame.get("principal")` (line ~1154) as the executing
  identity: the principal is the locally configured pairing principal; a frame that carries a
  `principal` different from it is refused with 0 invocations, an equal or absent one executes under
  the local principal (F2); (c) replace every interpolation of peer-supplied strings (error codes,
  reasons, action names, free text) in diagnostics and supervision records with locally defined
  labels — a closed mapping of known codes to labels, anything else rendered as a fixed
  `unrecognized` label, never the peer value (F5); (d) add two settings with conservative defaults
  validated in the manifest schema (`max_running_calls`, `max_duplicate_waiters`, finite positive
  ints, optional so no shipped profile gains a required setting): a received call beyond the
  running bound is refused with a terminal status (e.g. `busy`), and duplicate-call waiters (the
  futures at lines ~1096/~1356) beyond their bound are refused rather than queued (F14).
- **Dependencies:** [P1] (the executor's in-flight coalescing is what duplicate waiters defer to)
- **Tests:** In `tests/test_proxy.py`: AC6 out-of-set action refused, 0 executor invocations; in-set
  executed once. AC7 mismatched `principal` refused with 0 invocations; equal `principal` executes and
  the `ActionCall` the executor receives carries the locally configured principal. AC8 peer error
  frame whose code is the pairing token, and separately a 4 KiB string: neither occurs as a substring
  in captured diagnostics or supervision records. AC9 with running bound N=2 and a gated fake
  provider, a third concurrent call is refused with a terminal status, the executor sees exactly 2
  invocations, and a sampled count of live waiter tasks never exceeds the bound. Each run against
  `8010edc` (fails).
- **Risks:** Non-trivial (remote-party boundary). New settings change the manifest schema — P6's
  unknown-key refusal must know them; keep them declared in `module.yaml`. The config-UI module
  presentation tests (`tests/test_manifest_presentation.py`, `tests/test_config_ui.py`) may count
  settings; extend fixtures, never loosen. Dropping peer text must not remove the local reason code
  operators need — keep the label set informative.

### P4: Proxy caps unpaired connections and enforces a hello deadline (R6; F13, F19; AC20)
- **Files:** [`modules/proxy/__init__.py`, `modules/proxy/module.yaml`, `tests/test_proxy.py`]
- **Description:** In the proxy's connection handler (`modules/proxy/__init__.py`, the pre-`welcome`
  path around line 1045 where only `hello` is accepted), count connections that have not completed
  pairing. Add optional settings `max_unpaired_connections` and `hello_deadline_seconds` (finite,
  positive, conservative defaults, declared in `module.yaml`). A new connection beyond the cap is
  refused with a close code and a local diagnostic; a connection that has not sent a valid `hello`
  when the deadline elapses (measured on the module's injectable clock) is closed. The counter is
  decremented on pairing, close and error in a `finally`. One converged defect, two lenses: F13 (cap)
  and F19 (deadline) close together. Update the session-rules docstring (lines ~38–48) and
  `docs/proxy-protocol.md` / `docs/proxy-protocol.fr.md` only if they state the unbounded behaviour
  (kept a translation pair).
- **Dependencies:** [P3] (both steps edit `tests/test_proxy.py`; P4 adds its AC20 tests on top of
  P3's agent_link tests so the shared test-file changes are integrated in order)
- **Tests:** In `tests/test_proxy.py`: AC20 with cap N=2, a third unauthenticated connection is
  refused; a silent connection is closed once the deadline elapses on a manual clock and is not closed
  one tick before. Counter returns to 0 after closes (no leak). Run against `8010edc` (fails).
- **Risks:** Off-by-one between "accepted then closed" vs "refused"; a legitimate agent slow to send
  `hello` under load could be closed — defaults must be generous. Doc changes ripple into
  `tests/test_docs_bilingual.py`.

### P5: One credential-redaction primitive for every settings/configuration diagnostic, enforced structurally (R3; F4; AC10, AC11)
- **Files:** [`core/contracts.py`, `core/loader.py`, `core/main.py`, `core/config_ui/__init__.py`, `core/runtime.py`, `modules/streamerbot/__init__.py`, `tests/test_redaction_surfaces.py`, `tests/test_loader.py`, `tests/test_main.py`]
- **Description:** Make `core.contracts.mask_known` (`core/contracts.py:2235`) the single primitive
  and route **field paths and key labels** through it, not only values/reasons: the loader's
  `_field_diagnostic`, `_module_diagnostic`, `_quoted`/`_bounded`/`_redact_known`
  (`core/loader.py:2242–2443`), the schema validator's error label/path construction in
  `core/contracts.py` (`_apply_object_schema` ~line 938 and the `SchemaError` label paths), the
  `--check-config` reporter in `core/main.py` (~line 930/1608), the config-UI Check/Save responses
  (`core/config_ui/__init__.py:1488`), supervision diagnostics (`core/runtime.py:363`) and any module
  settings validator that formats a settings key (Streamer.bot's settings validator for AC10). Local
  wrappers (`_redact_values`, the config UI's wrapper, `contracts._redact`) remain only as one-call
  delegations to `mask_known`. Add `tests/test_redaction_surfaces.py`: an AST walk over `core/` and
  `modules/` that enumerates every function constructing a configuration/settings diagnostic
  (raising `ContractError`/`SchemaError`/`ConfigError`-family exceptions or building diagnostic
  strings with a field path), asserts each passes its path/reason through `mask_known` or a
  registered delegating wrapper, and includes a self-test: a fixture source file formatting a field
  path without the primitive makes the structural check fail.
- **Dependencies:** []
- **Tests:** AC10 — profile with `review-secret-4521` as a credential value also used as a key under
  `modules.streamerbot.settings`: `--check-config` exits non-zero and stdout+stderr do not contain the
  string; same for the config-UI Check response and for supervision records (tests in
  `tests/test_main.py` and `tests/test_loader.py`, config-UI case via the existing Check harness).
  AC11 — `tests/test_redaction_surfaces.py` enumeration plus its self-test. Run the AC10 tests
  against `8010edc` (fail); the AC11 self-test is its own proof of failure.
- **Risks:** Non-trivial (cross-module invariant the type system cannot check). The AST
  enumeration can false-positive or miss surfaces built via helper indirection — document the
  heuristics and the allowlisted delegating wrappers in the test. Redacting key paths can make two
  diagnostics collide or become unreadable; keep the path skeleton and mask only the matching
  segment. Existing diagnostic-text assertions in `test_loader.py`/`test_main.py`/`test_config_ui.py`
  should stay equal when no secret is present; if one changes it is not allowlisted — stop and flag.

### P6: Unknown settings refused at top level and in every enabled module's settings (R3; F26; AC12)
- **Files:** [`core/contracts.py`, `core/loader.py`, `core/main.py`, `tests/test_loader.py`, `tests/test_main.py`, `tests/test_examples.py`]
- **Description:** Refuse any profile key unknown to the top-level schema (in `core/main.py`'s
  `load_config`/`check_config`) and to each enabled module's declared `settings_schema` (in the
  loader's `_manifest_settings_validator`, `core/loader.py:1853`), by treating an object schema
  without explicit `additionalProperties` as closed for profile validation (or by applying
  `additionalProperties: false` at the loader boundary), recursively in nested settings objects.
  Each refusal names the key's path, produced through `mask_known` (P5). Startup and
  `--check-config` share the path. Every shipped `*.example` profile (`config.yaml.example`,
  `config.server.yaml.example`, `agent.yaml.example`, `presence.yaml.example` and module-level
  examples) must still validate — if one carries an undeclared key, declare it in the owning
  manifest schema rather than editing the profile (constraint: no profile change).
- **Dependencies:** [P5] (paths routed through the primitive); [P3, P4] (their new settings are declared before closure)
- **Tests:** AC12 — misspelled `brodcaster_id` under `modules.twitch.settings`, and separately an
  unknown top-level key: `--check-config` exits non-zero naming the path; parametrized over every
  shipped `*.example` profile: exit 0. A credential used as an unknown key is redacted (joins AC10).
  Run against `8010edc` (fails).
- **Risks:** Non-trivial (public config contract change). Test fixture profiles across the suite
  (`tests/fixtures/`, `test_integration.py`, `test_main.py`, `test_proxy_process`-style tests — see
  memory on profile ripples) may carry stray keys; fix the fixtures, never the assertions, and list
  every touched fixture in the commit. Manifests that rely on open objects for free-form maps
  (e.g. destination or rule maps) need explicit `additionalProperties: true` or
  `patternProperties` handling — check each module's schema before closing.

### P7: `!forgetme` erasure reaches the brain's conversation memory (R4; F6; AC13, AC14)
- **Files:** [`modules/brain/__init__.py`, `modules/viewer_memory/__init__.py`, `modules/viewer_memory/module.yaml`, `modules/brain/module.yaml`, `tests/test_brain.py`, `tests/test_viewer_memory.py`]
- **Description:** Use the existing service mechanism (the brain already publishes its scheduler as a
  service resolved by watch) rather than a bus event, so no erasure signal enters bus history: the
  brain publishes an erasure service (`forget(platform, channel_id, viewer_id)`), and viewer_memory's
  `forget` path (`modules/viewer_memory/__init__.py:1271`) resolves it and calls it after its own
  file erasure completes (absent brain → no-op, recorded in a local diagnostic with no viewer text).
  In the brain, `ConversationMemory` (`modules/brain/__init__.py:1747`) gains a per-key erasure epoch:
  `forget(key)` (line 1855) clears the exchanges and bumps the key's epoch; each run records the
  epoch at admission, and `record` for that key refuses (drops, counts) a record whose admission
  epoch is older than the current one. The erasure call carries only the key, never message text;
  the existing `memory.removed` supervision fact keeps carrying `reason`/`count` only. Declare the
  service relation in the manifests if the loader requires it (structural decision flagged in the
  commit message: service over event, because bus history must not carry the key's text and the
  service path matches watch↔brain).
- **Dependencies:** []
- **Tests:** In `tests/test_brain.py`/`tests/test_viewer_memory.py` (integration through a runtime
  with both modules): AC13 record an exchange, run `!forgetme` for the key → `recall(key) == ()`,
  the next prompt built for that viewer contains none of the erased text, another key's recall is
  unchanged. AC14 a run admitted before erasure whose record lands after it leaves `recall(key)`
  empty; bus history and audit records produced by the erasure contain none of the erased text. Run
  against `8010edc` (fails).
- **Risks:** Non-trivial (module boundary, ordering invariant). Epoch maps must be bounded (evict
  epochs with the memory's own LRU or keep only for keys erased within the run horizon). Ordering
  between viewer_memory's async file deletion and the brain call: call the brain after the file
  erasure is committed, but a failure of either must not leave the other silently done — report.
  If the loader requires module dependency declarations for services, an optional resolve keeps
  profiles without brain valid.

### P8: Streamer.bot owner task survives a raising supervision call (R5; F8; AC15)
- **Files:** [`modules/streamerbot/__init__.py`, `tests/test_automation_bridge.py`]
- **Description:** In the bridge's owner loop (`_attempt` at `modules/streamerbot/__init__.py:2390`,
  `_failed` at 2414, and the loss path), wrap each supervision call (`emit`, degraded/ready reports,
  reconcile trace) so an exception from supervision does not propagate out of the owner task: on such
  an exception the current connection is closed and cleared, `degraded` is reported if a further
  report succeeds (its own failure is caught and dropped after the connection is cleaned), and the
  reconnect loop continues under the existing backoff (the documented reset behaviour at line ~2328,
  `claude:F8`, is not changed). The catch is narrow to the supervision call sites and never swallows
  `asyncio.CancelledError`.
- **Dependencies:** [P5] (both steps edit `modules/streamerbot/__init__.py`: P5 routes its settings
  diagnostics through the redaction primitive; P8 changes supervision handling on top of that state)
- **Tests:** AC15 — with a supervision `emit` that raises during the reconcile trace, the owner task
  is alive after the failure, the fake connection is closed, and the fake instance sees connect count
  == 2 on the next attempt (manual clock). Run against `8010edc` (fails).
- **Risks:** Swallowed-error shortcut risk: the handler must close the connection and continue the
  loop (root cause handling), and must not catch `CancelledError`/`BaseException`. A permanently
  raising supervision must not create a hot loop — backoff applies.

### P9: Twitch input recovery and output bounds — terminal 401/403, keepalive-bounded receive, 500-char limit (R5, R6; F9, F10, F24; AC16, AC17, AC23)
- **Files:** [`modules/twitch/__init__.py`, `tests/test_twitch.py`]
- **Description:** (F9) When the chat subscription POST is answered 401 or 403 (the `400 <= status <
  500` branch around line 778), mark recovery terminal: report one `module.degraded` with an
  authentication reason and stop the reconnect loop — no further socket opened, no further
  subscription POST — until the module is restarted. (F10) Read `keepalive_timeout_seconds` from the
  welcome; bound every EventSub `receive` by `keepalive + fixed margin` (named constant, e.g. 5 s) on
  the module's clock; on expiry emit a diagnostic and run the existing loss/recovery path (new socket).
  (F24) In the `chat.write` provider, refuse text longer than 500 characters (named constant,
  measured in Unicode code points as Twitch counts) with a refusal observation before any request.
- **Dependencies:** [P2] (same file; the compatibility route now also reaches the 500-char check through `chat.write`)
- **Tests:** AC16 fake Helix answering 401, and separately 403, to the chat subscription → exactly
  1 `module.degraded` with an authentication reason; over a simulated 60 s, socket-open and
  subscription-POST counts stay at 1. AC17 welcome with `keepalive_timeout_seconds: 10` then silence →
  no recovery before 10 s, recovery (new socket + diagnostic) within 10 s + margin on a manual clock.
  AC23 501 characters → refusal and 0 POSTs; exactly 500 → 1 POST. Each against `8010edc` (fails).
- **Risks:** The degraded-then-ready interplay with the coordinator (memory: coordinator re-reports
  ready after degraded in start_inputs; failures name the module only — put the reason on
  `module.degraded`). Keepalive-bounded receive must not fire during a legitimate `session_reconnect`
  handover. 401 on token expiry might be recoverable by refresh in future — documented as terminal
  per spec.

### P10: YouTube does not admit the first page of a freshly resolved live chat (R5; F11; AC18)
- **Files:** [`modules/youtube/__init__.py`, `tests/test_youtube.py`]
- **Description:** In the poller (page-token loop near `modules/youtube/__init__.py:1367` and the
  403/404 re-lookup at line 1380), mark a poller as "priming" whenever the live chat id is freshly
  resolved (start and after a re-lookup). The first successful list in priming state records its
  `nextPageToken` and message ids for dedup but publishes none; subsequent pages are admitted
  normally.
- **Dependencies:** []
- **Tests:** AC18 fresh resolution whose first list returns 3 existing messages → 0 admitted; a
  message on the following page → count == 1; the same sequence after a 403 re-lookup. Run against
  `8010edc` (fails).
- **Risks:** A message sent between resolution and first list is dropped — acceptable and matching
  the spec. Ensure the priming flag resets on every re-resolution path (including 404) and not on a
  rate-limit 403.

### P11: Brain bounds model replies in bytes and survives deeply nested tool arguments (R6; F12, F20; AC19, AC21)
- **Files:** [`modules/brain/__init__.py`, `modules/brain/module.yaml`, `tests/test_brain.py`, `tests/test_model_adapter.py`]
- **Description:** (F12) Read the model HTTP response body with a byte bound (optional setting
  `max_response_bytes`, conservative default, declared in `module.yaml`); read at most bound+1 bytes
  and, if the body exceeds the bound, fail the run with an observable error (run failure reason
  `response_too_large`) without calling `json.loads` (`modules/brain/__init__.py:4660`). (F20) Wrap
  the tool-argument decode and any recursive walk of decoded arguments so `RecursionError` (and a
  depth beyond a fixed max) yields the `malformed_arguments` observation in a run and
  `BrainModuleError` in the probe.
- **Dependencies:** [P7] (same file; avoids conflicting edits in `ConversationMemory`/run code)
- **Tests:** AC19 fake model returning bound+1 bytes → run fails with the observable error and the
  JSON decoder is never called (spy on the decode hook); body exactly at the bound is decoded. AC21
  arguments nested 10,000 levels → `malformed_arguments` observation and the run does not end
  `run_failed`; same input to the probe raises `BrainModuleError`. Run against `8010edc` (fails).
- **Risks:** Catching `RecursionError` must be narrow (decode/walk only), not a blanket except.
  Streaming responses: bound applies to the full body; content-length header may lie, so count read
  bytes.

### P12: Admission's ready structure never retains removed sessions (R6; F23; AC22)
- **Files:** [`core/admission.py`, `tests/test_admission.py`]
- **Description:** In `core/admission.py`, when a session is removed by expiry or eviction (the
  paths around the `_max_sessions` check at line ~847), remove it from `self._ready` (deque, line 756)
  as well, or switch `_ready` to an insertion-ordered dict/set-backed structure with O(1) removal while
  preserving FIFO fairness. The invariant `len(_ready) <= max_sessions` holds at all times.
- **Dependencies:** []
- **Tests:** AC22 — with workers busy, 3 × `max_sessions` sessions expire or are evicted; assert
  `len(ready) <= max_sessions` throughout and that a later legit session is still scheduled in FIFO
  order. Run against `8010edc` (fails).
- **Risks:** Non-trivial (ordering/fairness invariant). Changing the structure must keep existing
  fairness tests in `tests/test_admission.py` and `tests/test_budgets.py` green unchanged. The watch
  module resolves the brain's scheduler (memory) — in-flight is per session; check watch tests too.

### P13: Kick dedup window covers the full accepted signature span (R6; F25; AC24)
- **Files:** [`modules/kick/__init__.py`, `modules/kick/module.yaml`, `tests/test_kick.py`]
- **Description:** Raise the lower bound of `_DEDUP_WINDOW_BOUNDS` (`modules/kick/__init__.py:641`)
  to `2 * TIMESTAMP_TOLERANCE_SECONDS` (= 600 s, derived from the constant at line 222, not a
  literal), mirror it in the manifest schema, and make sure `DEFAULT_DEDUP_WINDOW_SECONDS` ≥ 600.
  Check every shipped `*.example` profile: if one sets `window_seconds` below 600, flag it (the
  default-change constraint) rather than silently editing it.
- **Dependencies:** []
- **Tests:** AC24 — `window_seconds: 300` refused naming `window_seconds`; `600` accepted; with 600,
  a delivery signed 290 s ahead is accepted, the same delivery replayed 580 s later (290 s behind) is
  deduplicated, while a fresh message id with the same timestamp at the same instant is accepted.
  Run against `8010edc` (fails).
- **Risks:** Larger window increases dedup memory; `max_entries` still bounds it. If the shipped
  default is below 600 this is a default change — allowed only because the finding is that default;
  note it in the commit.

### P14: Viewer-memory consistency — no orphan evictions, temp-file reclaim, anchored name scan (R7; F21, F22, F31; AC25, AC26, AC27)
- **Files:** [`modules/viewer_memory/__init__.py`, `tests/test_viewer_memory.py`]
- **Description:** (F21) Restructure the write so evictions selected for space are performed only
  after the atomic write succeeds; on any `OSError` from the write, no eviction is executed (or, if
  some were already executed, publish one `memory.removed` with `count` = files actually deleted).
  Prefer "write first, evict after" so the first branch of AC25 holds. (F22) The scan recognises the
  store's own temporary files with an anchored `re.fullmatch` pattern derived from the write's
  `.viewer-memory-*.tmp` naming (`_TEMPORARY_SUFFIX`, line 188), counts their size toward
  `max_total_bytes` and removes them; they are never read as records. (F31) Replace
  `MEMORY_FILE_PATTERN.match` (line 161, `$` matches before a trailing newline) with `fullmatch`
  (or `\Z`), so `<64 hex>.json\n` is foreign: neither read, counted nor deleted.
- **Dependencies:** [P7] (same file's `forget` path changed there)
- **Tests:** AC25 atomic write raising `OSError(ENOSPC)` after evictions were selected → every
  previously present file still on disk (or one `memory.removed` with exact count). AC26 a 1,000-byte
  `.viewer-memory-x.tmp` counted toward `max_total_bytes` and removed by the next scan. AC27
  `<64hex>.json\n` survives a scan unread, undeleted, uncounted. Run against `8010edc` (fail).
- **Risks:** Write-before-evict can transiently exceed `max_total_bytes` by one record — document.
  A temp file being written concurrently by an in-flight write must not be removed by a concurrent
  scan: scans and writes are serialized in the store, confirm and keep it so.

### P15: Bus retention-copy failure reported as PublicationError; tautological bus assertion replaced (R7, R9; F33, F32; AC28, AC36)
- **Files:** [`core/bus.py`, `tests/test_bus.py`]
- **Description:** (F33) Wrap the retention `deepcopy(event)` (`core/bus.py:214`) so a copy failure
  raises `PublicationError` naming the event type (chaining the original cause) and appends a history
  record of that publication's failure (a minimal, copy-safe record: type, failure marker, reason;
  no payload). (F32) In the allowlisted
  `tests/test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting` (line 379),
  replace only `assert EventBus(**_VALID_LIMITS) is not None` with an assertion on a property the
  parameters determine (e.g. the constructed bus's configured limits equal `_VALID_LIMITS`), extend
  its docstring to cite F32 and R9; the parametrized rejection assertions stay unchanged.
- **Dependencies:** []
- **Tests:** AC28 publishing a payload holding an object whose `__deepcopy__` raises →
  `PublicationError` naming the type; `history` contains the failure record. Run against `8010edc`
  (fails). AC36 `grep` in a test (or the P20 audit) that `tests/test_bus.py` has no `is not None` on a
  constructor result. F32 mutation evidence (AC37): mutate `EventBus.__init__` to ignore one limit
  argument (store a default) → replacement assertion fails; restore → passes.
- **Risks:** If `EventBus` exposes no read access to its limits, adding a read-only property is a
  small public API addition — flag it. History record format must match what `list_events()`
  consumers (audit, config UI) tolerate.

### P16: `audio_output.stop` returns as soon as the player exits (R7; F30; AC29)
- **Files:** [`modules/audio_output/__init__.py`, `tests/test_audio_output.py`]
- **Description:** In `stop(process, grace_seconds)` (`modules/audio_output/__init__.py:691`),
  replace the unconditional grace sleep with `asyncio.wait_for(process.wait(), grace_seconds)`
  (or a race between `wait()` and the grace timer), killing only on timeout, so the call returns as
  soon as the player has exited.
- **Dependencies:** []
- **Tests:** AC29 fake player exiting 0.01 s after SIGTERM with grace 5 s → `stop` returns no later
  than 0.1 s after the recorded exit time (measured from the exit). A never-exiting player is still
  killed at the grace. Run against `8010edc` (fails).
- **Risks:** Real-time measurement flakiness — use the fake's recorded exit timestamp and a
  monotonic clock; avoid tight wall-clock bounds beyond the spec's 0.1 s.

### P17: Config-UI Check releases its loop by the validator deadline (R7; F35; AC30)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`]
- **Description:** In the Check's private loop runner (`core/config_ui/__init__.py` ~5300–5322: the
  validation run, then the cleanup `run_until_complete(asyncio.gather(*pending, ...))`,
  `shutdown_asyncgens`, `shutdown_default_executor`), compute **one absolute deadline** when the
  Check starts: `start + loader validator deadline + 1 s` (the loader's existing deadline constant,
  not a new literal), read from the injectable clock. Validation and every cleanup stage share that
  single deadline: each stage waits only for the time *remaining* until it (`max(0, deadline - now)`),
  never a fresh per-stage budget, so sequential stalled stages cannot accumulate beyond the bound —
  in particular, after validation has already timed out, cleanup gets only the residual ≤ 1 s in
  total. When the remaining time reaches zero, the runner skips the stages not yet run, abandons
  remaining tasks without awaiting them further, closes the loop (never calling a blocking
  `shutdown_default_executor` past the deadline) and reports the timeout in the Check response.
- **Dependencies:** [P5] (same file's Check response path changed there)
- **Tests:** AC30 — a validator that never returns → Check releases its loop within deadline + 1 s
  (measured from Check start) on a manual clock and the response reports the timeout. Additional
  cases exercising several stalled stages against the one deadline: (a) validator times out, then a
  pending task ignoring cancellation, an async generator whose `aclose` stalls and a default-executor
  job that never finishes are all present — total elapsed from Check start ≤ deadline + 1 s, not
  deadline + 3 s; (b) validation finishes early but two cleanup stages each stall — they share the
  remaining budget, total still ≤ deadline + 1 s; (c) a normal Check completes every stage and
  reports no timeout. Run against `8010edc` (fails).
- **Risks:** A validator stuck in a thread (default executor) cannot be cancelled — the runner must
  not await `shutdown_default_executor` past the shared deadline (abandon the executor without
  waiting). A per-stage timeout reintroduced by refactoring would silently break the overall bound —
  test (a) pins it. Keep Check's restoration semantics (memory: Check shares restoration, not
  protection).

### P18: Installation path proven by the code — endpoint URLs, overlay name, README command, command-resolution tests (R8; F15, F16, F17, F18; AC31, AC32, AC33)
- **Files:** [`docs/installation.md`, `docs/installation.fr.md`, `README.md`, `tests/test_installation_doc.py`]
- **Description:** (F15) Both guides' `MODEL_ENDPOINT` example values become complete Chat
  Completions URLs ending in `/chat/completions`. (F17) Guides and README name the overlay file the
  config UI writes for the guide's profile (`mon-compagnon.local.yaml` for `mon-compagnon.yaml`).
  (F16) The README's configuration-UI command is replaced by one that runs (the
  `[project.scripts]` console script from `pyproject.toml`, or `python -m core.config_ui` if that
  module has a `main`). (F18) In `tests/test_installation_doc.py`, replace the grep in the
  allowlisted `test_the_guide_prints_the_commands_the_code_still_has` with command resolution: every
  `python -m X` and console-script command extracted from both guides and the README resolves (`X`
  importable with a callable entry point, or a `[project.scripts]` name); docstring cites F18 and R8.
  Add: the README's config-UI command run as a subprocess exactly as printed with arguments
  replaced by `--help`, exit 0 within 30 s, usage naming `--config`; the endpoint test starts the
  guide's profile against a fake model server and asserts the posted URL equals the guide's value;
  the overlay test computes the name from `core/overlay.py` rather than a literal. FR guide kept a
  translation pair.
- **Dependencies:** []
- **Tests:** AC31, AC32, AC33 as above; the resolution test fed the pre-fix README line
  `python -m twitch-ia-compagnon-config-ui` fails; the subprocess check on the pre-fix README command
  exits non-zero. Run the new tests against `8010edc` (fail). `tests/test_docs_bilingual.py` and
  `tests/test_phase5_docs_status.py` pass unchanged.
- **Risks:** Subprocess test requires the package to be installed (console script on PATH) in CI —
  fall back to `sys.executable -m` form only if that is exactly what the README prints. Profile
  change ripple (memory): F15's endpoint value is allowed to change `*.example` only if the finding
  is that default; fixtures in `test_integration`/`test_main` may follow — fix fixtures, not
  assertions.

### P19: Brain history-budget/order assertions and a self-contained bridge AC16 test (R9; F27, F28; AC34, AC35)
- **Files:** [`tests/test_brain.py`, `tests/test_automation_bridge.py`]
- **Description:** (F27) Add brain tests that pin, independently: the history token budget (history
  beyond budget is dropped), the 50% output reservation (prompt budget leaves half the context for
  output) and chronological order of history in the built prompt. (F28) Replace the assertions of the
  allowlisted `test_ac16_no_run_observation_is_a_success` (`tests/test_automation_bridge.py:2216`) so
  the test creates its own fixture runtime, drives at least one run of the action, records ≥ 1
  observation, and asserts none is `success`; extend its docstring (line 2217) to cite F28 and R9.
  No production code changes in this step.
- **Dependencies:** [P11] (brain behaviour settled before pinning tests); [P8] (bridge test file touched there)
- **Tests:** AC34 — evidence in the commit: three mutations of `modules/brain/__init__.py` (remove
  the token budget, remove the 50% reservation, reverse history order), each making ≥ 1 new test fail,
  then restore → pass. AC35 — run the test alone, with `-p no:randomly` and under random order: ≥ 1
  observation exercised; mutation making the bridge report the run as `success` → fails alone;
  restored → passes.
- **Risks:** Test-defect mutation evidence must be recorded honestly (commands + output). A
  self-contained test must not depend on module-level state set by other tests — audit fixtures.

### P20: Phase summary, AC37 evidence ledger and AC38 diff audit (R10; all findings F1–F35, `claude:F8`; AC37, AC38, AC39 summary half)
- **Files:** [`docs/campaigns/phase6/summary.md`]
- **Description:** Write the summary with 36 entries (F1–F35 and `claude:F8`): for each closed
  finding the closing commit, the regression test id and the evidence form (pre-fix run on
  `8010edc` for product defects; mutation for F27, F28, F32); for F34 and `claude:F8` the
  out-of-scope reason from the spec/brief. Audit the phase diff (`git diff 8010edc..HEAD -- tests`)
  for added `skip`/`xfail`, deleted or loosened assertions; record that the only replaced assertions
  are the allowlisted tests with their docstrings. Run the full suite and `tests/test_phase5_docs_status.py`
  and record the counts.
- **Dependencies:** [P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14, P15, P16, P17, P18, P19]
- **Tests:** Full suite green; a scripted re-run of every regression test id listed in the summary
  against `8010edc` (product defects fail) and at HEAD (pass), outputs captured in the summary.
- **Risks:** A regression test that unexpectedly passes on `8010edc` means it does not prove the fix
  — return to its step. Commit hashes must be final (write after the last fix commit, amend only
  the summary).

### P21: Full-branch review gate (R10; AC39 gate half; all of R1–R10)
- **Files:** [`docs/campaigns/phase6/full-branch-review.md`]
- **Description:** Run a full-branch review by the reviewer model over `8010edc..HEAD` with **all
  changed files together**, as phase 5 did (`docs/campaigns/phase5/full-branch-review*.md`). Per-commit
  reviews miss cross-file bugs by construction: each commit can be correct in isolation yet broken in
  combination — e.g. P1's identity-bound retention with P3's agent waiters, P2's executor route with
  P9's 500-char provider check, P5's redaction with P6's unknown-key paths, P7's erasure service with
  P14's store rewrite — so only a review of the integrated diff can see those interactions. The
  review re-runs the original report's reproductions for every finding claimed closed, records their
  outputs, and must end `APPROVE`; any `REQUEST_CHANGES` loops back to the owning step and the
  report is re-tracked as `full-branch-review-2.md`, etc.
- **Dependencies:** [P20]
- **Tests:** Every reproduction from `docs/reviews/2026-10-16-full-adversarial-review.md` for closed
  findings re-run and recorded; full suite green at the reviewed HEAD.
- **Risks:** Gate fatigue — fix rounds may modify earlier steps' files; the summary (P20) must be
  updated with any new closing commit.

## Ordering rationale

Steps follow the review's priority order (report group 1, security boundaries, first), as the brief
requires, constrained by dependencies. P1 comes first because the executor's identity-bound
retention and in-flight coalescing are the contract both P2 (the Twitch compatibility route becomes
an executor call) and P3 (agent_link duplicate waiters and principal fixing) build on. P4 (proxy)
closes the other remote-party boundary and follows P3 because both edit `tests/test_proxy.py`. P5 establishes the single redaction
primitive before P6 introduces new unknown-key diagnostics, which must go through it; P6 also waits
for P3/P4 so their new settings are declared before schemas close. P7 (erasure) precedes P11 and
P14, which edit the same brain and viewer-memory files. P8–P13 are the input-owner liveness and
untrusted-input bounds; P8 follows P5 because both edit `modules/streamerbot/__init__.py`; P9 follows P2 because both edit `modules/twitch/__init__.py`. P15–P17 are
local-state consistency fixes; P17 follows P5 for the shared config-UI file. P18 (docs) is
independent. P19 (test-only R9) comes after the brain and bridge code have settled so its mutations
pin final behaviour. P20 records evidence once every closing commit exists, and P21 is the
full-branch gate over the integrated diff, last by construction.

## Plan-review findings addressed

- `P1` (shared `tests/test_proxy.py`, P3/P4 unordered) → P4 now depends on [P3].
- `P2` (shared `modules/streamerbot/__init__.py`, P5/P8 unordered) → P8 now depends on [P5].
- `P3` (per-stage cleanup timeouts exceed the R7/AC30 overall bound) → P17 uses one absolute deadline
  shared by validation and all cleanup stages, with tests over multiple stalled stages.
