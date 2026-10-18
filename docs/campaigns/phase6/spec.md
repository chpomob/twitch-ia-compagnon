---
name: "phase6-adversarial-review-fixes"
version: "1.0"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec, phase6, security, integrity, review-fixes]
targets:
  - file: modules/twitch/__init__.py
    description: "Compatibility channel.chat.send route invokes the shared executor under default-deny (F1); refused 401/403 chat subscription is terminal and reported degraded (F9); EventSub receive bounded by the welcome keepalive (F10); chat.write refuses over-length text before any request (F24)."
  - file: core/actions.py
    description: "Retained outcomes bound to the full call identity, not call_id alone, and checked only after authorization (F3); concurrent duplicate call_id reaches the provider once (F7); cancel_grace_seconds must be finite (F29)."
  - file: modules/agent_link/__init__.py
    description: "Received calls refused outside the negotiated action set; the principal is fixed by the pairing, never read from the peer frame (F2); peer-supplied text never enters diagnostics (F5); running executions and duplicate-call waiters bounded (F14)."
  - file: core/loader.py
    description: "Every settings/schema diagnostic (field path and reason) passes through the single credential-redaction primitive (F4); unknown settings refused (F26)."
  - file: core/contracts.py
    description: "Settings-schema validation reports unknown properties at top level and in every module's settings (F26) through the redaction primitive (F4)."
  - file: modules/viewer_memory/__init__.py
    description: "!forgetme erasure reaches the brain (F6); evictions not performed for a write that fails (F21); leaked temporary files reclaimed and counted (F22); scan pattern anchored at true end of name (F31)."
  - file: modules/brain/__init__.py
    description: "Forgets ConversationMemory for an erased key and refuses records from runs admitted before the erasure (F6); model response bounded in bytes before JSON decoding (F12); nested tool arguments yield malformed_arguments, probe raises BrainModuleError (F20)."
  - file: modules/streamerbot/__init__.py
    description: "A raising supervision call in the attempt/fail/lose paths no longer kills the owner task; connection closed, degraded reported, loop continues (F8)."
  - file: modules/youtube/__init__.py
    description: "The first page of a freshly resolved live chat is not admitted as new messages (F11)."
  - file: modules/proxy/__init__.py
    description: "Unpaired connections capped and closed after a hello deadline (F13, F19)."
  - file: core/admission.py
    description: "Sessions dropped by expiry/eviction leave the ready structure, which stays bounded by max_sessions (F23)."
  - file: modules/kick/__init__.py
    description: "Dedup window lower bound covers the full accepted signature span (F25)."
  - file: core/bus.py
    description: "Retention copy failure reported as a PublicationError with a history record, not a raw TypeError (F33)."
  - file: modules/audio_output/__init__.py
    description: "stop returns as soon as the player has exited instead of sleeping the full grace (F30)."
  - file: core/config_ui/__init__.py
    description: "Check's loop cleanup bounded by the loader's validator deadline (F35)."
  - file: docs/installation.md
    description: "MODEL_ENDPOINT values are complete Chat Completions URLs (F15); overlay file named as the code writes it (F17)."
  - file: docs/installation.fr.md
    description: "Same corrections as the English guide (F15, F17), the pair staying a translation pair."
  - file: README.md
    description: "Configuration-UI command is one that runs (F16)."
  - file: tests/test_installation_doc.py
    description: "Commands printed in both guides and the README are executed or resolved against the code, not grepped (F18)."
  - file: tests/test_twitch.py
    description: "Compatibility-route tests configure the grant they need; regressions for F1, F9, F10, F24."
  - file: tests/test_actions.py
    description: "Regressions for F3, F7, F29."
  - file: tests/test_proxy.py
    description: "agent_link regressions for F2 (out-of-set action, peer-chosen principal), F5, F14, and the proxy cap/deadline (F13, F19)."
  - file: tests/test_redaction_surfaces.py
    description: "New structural test enumerating every diagnostic-emitting consumer of settings/credentials and asserting each routes through the one redaction primitive (F4)."
  - file: tests/test_brain.py
    description: "Regressions for F6, F12, F20, and assertions on the history token budget, 50% output reservation and chronological order (F27)."
  - file: tests/test_automation_bridge.py
    description: "test_ac16_no_run_observation_is_a_success made self-contained (F28)."
  - file: tests/test_bus.py
    description: "Tautological constructor assertion replaced by a real one (F32); regression for F33."
  - file: docs/campaigns/phase6/summary.md
    description: "New phase summary: per finding, the commit and test that closes it or the reason it stays open."
  - file: docs/campaigns/phase6/full-branch-review.md
    description: "New closing-gate report of the full-branch review, ending APPROVE, with re-run reproductions."
---

# Phase 6 — closing the defects of the full adversarial review

## Problem

The full adversarial review of `2026-10-16` (`docs/reviews/2026-10-16-full-adversarial-review.md`,
the authority for this phase) found that the kernel's discipline holds but that the defects that
matter sit **on the boundaries between modules**: a compatibility route that skips the executor, an
agent that trusts the `principal` its peer chooses, an observation cache keyed by `call_id` alone, a
settings diagnostic that echoes a credential used as a key, an erasure that stops at the module that
owns the file. Both reviewers returned `REQUEST_CHANGES`. Phase 6 closes those defects **as
boundaries**: each requirement below states a contract one boundary must hold for every caller, so a
fix at one spot that leaves a sibling path open does not satisfy it.

### Finding accounting (assumption, documented)

The report's tables number 35 rows (`#1`–`#35`); this spec cites them as `F1`–`F35`. The brief's
"Out of scope" excludes the two findings challenged in cross-review: `F34` (`codex:F6`, the stalled
trace subscriber, which needs an out-of-tree subscriber) and `claude:F8` (the Streamer.bot backoff
reset, documented behaviour at `modules/streamerbot/__init__.py:2328`, not a table row). Every other
row — **34 findings: F1–F33 and F35** — is in scope. `F13` and `F19` are one converged defect seen
through two lenses (proxy admission cap and hello deadline) and are closed together by R6. The phase
summary still lists F34 and `claude:F8` with the reason they are not fixed, so all 35 distinct
defects have a recorded disposition.

### Binding constraints carried from the brief

- Phase-5 decisions stay binding: the bridge is optional; its events replace the basic inputs and
  never run beside them; an execution the instance cannot confirm is never a success;
  authorization is default-deny; an input pair has exactly one owner.
- `docs/design-v2.md:584` ("output compatibility does not bypass the executor") is the contract R1
  makes true.
- No shipped `*.example` profile gains a required setting, a dependency or a changed default path,
  except where the finding *is* that default (F15's endpoint value, F16's command, F17's file name).
- The bridge stays "not delivered"; `tests/test_phase5_docs_status.py` keeps passing unchanged.
- No real platform, Streamer.bot instance, OBS, model service or audio device is contacted; all
  regressions run against the in-tree fakes.

## Requirements

- R1: **The executor is the only path from a caller to a provider, and its replay protection binds
  the whole call.** (F1, F3, F7, F29) A `channel.chat.send` compatibility publication reaches the
  Twitch transport only as an executor invocation of `chat.write` carrying an explicit principal and
  destination, so default-deny applies: with no applicable rule it is refused and no Helix request is
  emitted, and its legacy `delivery_status`/`delivery_outcome` metadata reports the refusal as
  `failed` with the executor's terminal status. A retained outcome is returned for a reused
  `call_id` only when the reuse is the same logical call (same principal, session, action,
  destination and arguments) and only after that call is authorized; any other reuse is refused
  without invoking the provider and without disclosing the retained observation. Two concurrent
  invocations of the same logical call reach the provider at most once and both callers receive the
  same terminal observation. `cancel_grace_seconds` must be a finite non-negative number.
- R2: **A paired peer is bounded by the pairing, not by what it sends.** (F2, F5, F14) The agent
  executes a received call only if its action is in the action set negotiated at pairing (the
  intersection of what was offered and what is configured); any other action is refused without
  reaching the executor. The principal under which a received call is executed is fixed locally by
  the pairing configuration; a `principal` field in the peer's frame is never used as the executing
  identity, and a frame whose `principal` differs from the paired one is refused. No peer-supplied
  string (error code, reason, action name, any free text) enters the agent's diagnostics or supervision
  records verbatim; diagnostics carry only locally defined labels. The number of concurrently running
  received executions and of duplicate-call waiters is bounded, and a call beyond the bound is
  refused with an observable terminal status rather than queued without limit.
- R3: **The credential-redaction class is closed by construction, and the settings boundary refuses
  what it does not know.** (F4, F26) There is exactly one redaction primitive for configured
  credential values, and every diagnostic produced from configuration or settings — field paths and
  key labels as well as reasons and messages — passes through it before leaving the process
  boundary (stderr, the config UI, supervision, logs, exception text). The set of consumers that
  emit such diagnostics is enumerated by a structural test over the source tree, so a new emitting
  surface that bypasses the primitive fails the suite. A profile key unknown to the top-level schema
  or to an enabled module's settings schema is refused by startup and `--check-config`, naming the
  key's path (redacted per this requirement); every shipped `*.example` profile still validates.
- R4: **An erasure reaches every holder of the erased viewer's text.** (F6) After `!forgetme`
  completes for a `(platform, channel, viewer)` key, the brain's in-process conversation memory for
  that key is empty, and a record for that key produced by a run admitted before the erasure is
  refused rather than re-creating the memory. The erasure signal must not carry the viewer's message
  text into bus history or the audit stream.
- R5: **An input owner never dies or stalls silently.** (F8, F9, F10, F11) A raising supervision
  call in the Streamer.bot bridge's attempt, failure or loss path does not end its owner task: the
  connection is closed and cleared, `degraded` is reported when reporting is possible, and the
  reconnect loop continues under its existing backoff. A 401 or 403 refusal of the Twitch chat
  subscription is terminal for recovery: the module reports `module.degraded` with an
  authentication reason and stops opening sockets and POSTing subscriptions. An EventSub socket
  silent for longer than the welcome's keepalive timeout plus a fixed margin is treated as lost and
  recovered, with a diagnostic. On the first list of a freshly resolved YouTube live chat (start, or
  after a 403/404 re-lookup) the messages already present are not admitted as new.
- R6: **Every input from an untrusted or remote party is bounded before it is processed.** (F12, F13,
  F19, F20, F23, F24, F25) The model response body is bounded in bytes before JSON decoding, and a
  reply exceeding the bound fails the run with an observable error. The proxy caps the number of
  unpaired connections and closes a connection that has not sent a valid hello within a deadline.
  Tool-call arguments nested beyond the decoder's capacity yield the `malformed_arguments`
  observation in a run and `BrainModuleError` in the probe, never an uncaught `RecursionError`.
  Admission's ready structure never retains a session that expiry or eviction removed, so its size
  stays ≤ `max_sessions`. Twitch `chat.write` refuses text longer than the platform limit of 500
  characters before emitting any request. Kick's minimum dedup window is at least the full span over
  which a signed delivery is accepted (2 × the 300 s timestamp tolerance = 600 s).
- R7: **Local state stays consistent across failures.** (F21, F22, F30, F31, F33, F35) A viewer-memory
  write that fails with any `OSError` deletes no file for that write, or publishes every deletion it
  performed as `memory.removed`. Temporary files left by an interrupted write are reclaimed by the
  scan and counted toward `max_total_bytes` until reclaimed. The scan reads, counts or deletes only
  two kinds of name: record files named exactly `<64 hex>.json`, and the store's own temporary
  files matching the write's `.viewer-memory-*.tmp` pattern (counted and removed, never read as
  records). Every other name, including `<64 hex>.json` with a trailing newline, is foreign and is
  neither read, counted nor deleted. A bus retention-copy
  failure is reported as a `PublicationError` naming the event type and leaves a history record of
  the failure. `audio_output.stop` returns within 0.1 s of the player's exit instead of always waiting
  the full grace. The config UI's Check releases its event loop no later than the loader's validator
  deadline plus 1 s, even if a validator never returns.
- R8: **The first-installation path is proven by the code, not by text.** (F15, F16, F17, F18) Both
  installation guides give `MODEL_ENDPOINT` values that are complete Chat Completions URLs ending in
  `/chat/completions`, and the guides and README name the overlay file the configuration UI actually
  writes for the guide's profile (`mon-compagnon.local.yaml` for `mon-compagnon.yaml`). The README's
  configuration-UI command runs. The documentation tests resolve every command printed in both guides
  and the README against the code (module importable with a `main`, or console script declared in
  `pyproject.toml`) and check the guide's endpoint value against the request URL a fake-backed startup
  actually posts to.
- R9: **Tests assert what their names claim.** (F27, F28, F32) The brain's history token budget, its
  50% output reservation and the chronological order of history are each asserted so that removing
  any one of them makes a test fail. `test_ac16_no_run_observation_is_a_success` passes or fails on
  its own, independent of test order, selection or parallelism. The tautological
  `EventBus(...) is not None` assertion is replaced by an assertion that can fail.
- R10: **Every closure is evidenced, and the phase is gated.** Each in-scope finding ships with a
  regression test that passes at the phase head and is proven able to fail, in the form AC37 sets
  by where the defect lives: a product defect's test fails on the pre-fix tree (`8010edc`); a test
  defect's (F27, F28, F32) new or replacement assertion fails under a stated mutation of the code under
  test — for F27 the three mutations of AC34, for F28 the bridge reporting a run as `success`, as
  AC35 names. No test is skipped,
  xfailed, deleted or loosened to turn red green, and a test that encoded a defect is replaced with
  a docstring citing the finding id and the requirement it restores, and only for a test named in
  the test failure allowlist. A replacement is at least as
  strong as what it replaces, which AC37 proves in the form that fits its finding. A
  docstring never makes a skip, an xfail, a deletion or a loosening acceptable. The phase summary lists, for each
  of F1–F35 and `claude:F8`, either the closing commit and test or the reason it remains open. A
  closing gate — a full-branch review by the reviewer model, its report tracked under `docs/campaigns/phase6/` as phase 5 did
  — re-runs the report's reproductions for every finding claimed closed and must end `APPROVE`.

## Acceptance criteria

- AC1 (R1): On a runtime with an executor and a profile with no rule granting `chat.write` to the
  compatibility principal, publishing `channel.chat.send {"text": "hi"}` emits 0 Helix POSTs, the
  provider's invoke count stays 0, and the returned metadata has `delivery_status == "failed"` and a
  `delivery_outcome` equal to the executor's refusal status; the same publication with a matching
  rule emits exactly 1 POST and reports `sent`.
- AC2 (R1): The compatibility publication produces exactly 1 `action.started` and 1
  `action.completed` trace carrying the compatibility principal and the Twitch destination — the same
  traces an executor call produces. (Amendment A1: this criterion said `action.requested`, a trace the
  kernel does not define; the canonical executor start trace is `action.started`
  (`core/contracts.py:149`), which is what its own closing clause already required. Recorded in
  `scaffolding/plan-amendments.md`, no code change.)
- AC3 (R1): After call `c1` by principal `brain` completes, an invocation reusing `c1` with a
  different principal, session, action, destination or arguments (5 parametrized cases) is refused,
  the provider count is unchanged, and the returned observation contains none of the original call's
  result fields; an invocation reusing `c1` that the policy refuses is refused even if identical.
- AC4 (R1): Two `invoke()` calls with the same logical call started concurrently (both awaiting
  before either completes) yield provider invocation count == 1 and handler run count == 1, and both
  callers receive observations with equal status and result.
- AC5 (R1): Constructing `ActionExecutor` with `cancel_grace_seconds` of `float("nan")`,
  `float("inf")` or `float("-inf")` raises `ContractError` naming `cancel_grace_seconds`; `0` and
  `0.5` are accepted.
- AC6 (R2): A received frame naming an action outside the negotiated set is refused with 0 executor
  invocations; an action in the set is executed once.
- AC7 (R2): A received frame whose `principal` differs from the paired principal is refused with 0
  executor invocations, and a frame whose `principal` equals it executes under the locally configured
  principal (asserted on the `ActionCall` the executor receives).
- AC8 (R2): A peer error frame whose code is set to the pairing token (and, separately, to a 4 KiB
  string) results in diagnostics and supervision records in which neither string occurs as a
  substring.
- AC9 (R2): With the running-execution bound set to N=2, a third concurrent received call is refused
  with a terminal status, the executor sees exactly 2 invocations, and the number of live
  waiter tasks never exceeds the configured bound.
- AC10 (R3): A profile with a credential `review-secret-4521` used as a key under
  `modules.streamerbot.settings` makes `--check-config` exit non-zero with output (stdout + stderr)
  not containing `review-secret-4521`; the same holds for the config-UI Check response and for
  supervision records.
- AC11 (R3): The structural test enumerates every module/function in `core/` and `modules/` that
  emits a configuration or settings diagnostic and asserts each routes it through the single
  redaction primitive; a fixture surface added that formats a field path without the primitive makes
  the test fail (demonstrated by a self-test of the structural test).
- AC12 (R3): A profile with a misspelled key under `modules.twitch.settings` (e.g. `brodcaster_id`)
  and, separately, an unknown top-level key, each make `--check-config` exit non-zero naming that
  key's path; every shipped `*.example` profile passes `--check-config` with exit code 0.
- AC13 (R4): After a viewer's exchange is recorded and `!forgetme` completes for that key, brain
  `recall(key)` returns an empty tuple, and the next prompt built for that viewer contains none of
  the erased text; other keys' recall is unchanged.
- AC14 (R4): A run admitted before the erasure that records after it leaves `recall(key)` empty; bus
  history and audit records produced by the erasure contain none of the erased message text.
- AC15 (R5): With a supervision `emit` that raises during the bridge's reconcile trace, the owner task
  is still alive after the failure, the fake connection is closed, and a second fake instance is
  connected on the next attempt (connect count == 2).
- AC16 (R5): A fake Helix answering 401 (and, separately, 403) to the chat subscription yields exactly
  1 `module.degraded` with an authentication reason, and over a simulated 60 s no further socket is
  opened and no further subscription POST is made (counts stay at 1).
- AC17 (R5): A fake EventSub socket that sends a welcome with `keepalive_timeout_seconds: 10` then
  stays silent causes a recovery (new socket opened, diagnostic emitted) within 10 s + the margin on
  a manual clock, and no recovery before 10 s.
- AC18 (R5): A fresh YouTube chat resolution whose first list returns 3 existing messages admits 0
  of them; a message on the following page is admitted (count == 1); the same holds after a 403
  re-lookup.
- AC19 (R6): A fake model returning a body one byte over the configured bound fails the run with an
  observable error and the JSON decoder is never called on it; a body at the bound is decoded.
- AC20 (R6): With the unpaired-connection cap at N=2, a third unauthenticated connection is refused;
  a connection that sends nothing is closed once the hello deadline elapses on a manual clock, and
  is not closed before it.
- AC21 (R6): Tool-call arguments nested 10,000 levels deep yield the `malformed_arguments`
  observation and the run does not end `run_failed`; the same input to the probe raises
  `BrainModuleError`.
- AC22 (R6): After 3 × `max_sessions` sessions expire or are evicted while workers are busy, the ready
  structure's length is ≤ `max_sessions`.
- AC23 (R6): Twitch `chat.write` with 501 characters returns a refusal with 0 POSTs; 500 characters
  emits 1 POST.
- AC24 (R6): Kick settings with `window_seconds: 300` are refused naming `window_seconds`;
  `window_seconds: 600` is accepted; with `window_seconds: 600`, a delivery whose signed timestamp is
  290 s ahead of the receiver clock is accepted, and the same delivery replayed 580 s later (signed
  timestamp then 290 s behind the clock, inside the 300 s tolerance at both deliveries) is
  deduplicated, while a delivery with a fresh message id and that same timestamp at that same instant
  is accepted (proving the signature check alone does not reject the replay).
- AC25 (R7): A viewer-memory write whose atomic write raises `OSError(ENOSPC)` after evictions were
  selected either leaves every previously present file on disk, or publishes one `memory.removed`
  whose `count` equals the number of files actually deleted.
- AC26 (R7): A `.viewer-memory-*.tmp` file of 1,000 bytes placed in the store is counted toward
  `max_total_bytes` and is removed by the next scan.
- AC27 (R7): A file named `<64 hex>.json\n` in the store survives a scan unread and undeleted, and
  is not counted.
- AC28 (R7): Publishing an event whose payload cannot be deep-copied raises `PublicationError` naming
  the event type, and history contains a record of that publication's failure.
- AC29 (R7): With a fake player that exits 0.01 s after SIGTERM and a grace of 5 s,
  `audio_output.stop` returns no later than 0.1 s after the player's recorded exit time (measured
  from the exit, not from the SIGTERM).
- AC30 (R7): A config-UI Check whose validator never returns releases its loop within the loader's
  deadline + 1 s on a manual clock, and the Check reports the timeout.
- AC31 (R8): Both guides' `MODEL_ENDPOINT` example values end in `/chat/completions`; a fake-backed
  startup of the guide's profile with the guide's value posts to exactly that URL.
- AC32 (R8): Both guides and the README name `mon-compagnon.local.yaml` (or the overlay name the code
  derives for the profile they use), and the test computes that name from the overlay code rather
  than a literal.
- AC33 (R8): Every `python -m X` and console-script command printed in both guides and the README
  resolves: `X` is importable and exposes a callable entry point, or the name is a
  `[project.scripts]` entry of `pyproject.toml`; the pre-fix README line
  `python -m twitch-ia-compagnon-config-ui` makes the test fail. In addition, the README's
  configuration-UI command, executed as a subprocess exactly as printed with its arguments replaced
  by `--help`, exits with status 0 within 30 s and prints usage text naming `--config`; the pre-fix
  README command exits non-zero under the same check.
- AC34 (R9): Each of three mutations — removing the history token budget, removing the 50% output
  reservation, reversing history order — makes at least 1 brain test fail (demonstrated per mutation
  in the step's evidence).
- AC35 (R9): `pytest tests/test_automation_bridge.py::test_ac16_no_run_observation_is_a_success`
  run alone and under `-p no:randomly`/random order exercises at least 1 recorded observation and
  fails if one of them is a success. The mutation that proves it (AC37's mutation form) makes the
  bridge report a run of the action as `success`: run alone under that mutation the test fails, and
  with the code restored it passes (demonstrated in the step's evidence).
- AC36 (R9): `tests/test_bus.py` contains no `is not None` assertion on a constructor result.
- AC37 (R10): For each in-scope finding, its regression test passes at the phase head and is proven
  able to fail, in one of two forms set by where the defect lives; the summary records the test id
  per finding and the evidence form used.
  - Product defect (every in-scope finding except F27, F28 and F32): the test fails when run against
    `8010edc`; the summary records that pre-fix run.
  - Test defect (F27, F28, F32), where the defect lies in test evidence and not in the product — a
    behaviour already present on `8010edc` that no test pins (F27: the history token budget, the 50%
    output reservation and the chronological order), or a vacuous or tautological assertion that
    would pass on `8010edc` as well (F28, F32): the new or replacement assertion is proven able to
    fail by a mutation of the code under test, the evidence standard AC34 uses — the step's evidence
    states the mutation, runs the test against it and shows the new assertion fail, then restores the
    code and shows it pass. AC34 supplies that evidence for F27 (its three mutations) and AC35 for F28
    (the bridge reporting a run as `success`). The summary records that mutation in place of a
    pre-fix run.
- AC38 (R10): The diff of the phase adds no `skip` and no `xfail`, deletes no assertion and loosens
  no assertion — without exception. The only change it makes to an existing test's assertions is a
  replacement of assertions that encoded the defect being fixed, and only for the tests named in the
  test failure allowlist; each such replaced test carries a docstring citing its finding id and
  requirement id (F1 and R1, F18 and R8, F28 and R9, or F32 and R9, as the allowlist assigns), and its
  replacement is at least as strong as what it replaces, which is established by AC37: a test whose
  assertions are replaced counts as a regression test of its finding, so for a product defect (F1,
  F18) it fails against `8010edc` and passes at the phase head, and for a test defect (F28, F32) it
  meets AC37's mutation form instead. An allowlisted test adapted only by a fixture that configures the
  grant it needs keeps its assertions unchanged and is not a replacement. A docstring never makes a
  skip, an xfail, a deletion or a loosening acceptable.
  `tests/test_phase5_docs_status.py` passes unchanged; the full suite is green.
- AC39 (R10): `docs/campaigns/phase6/summary.md` has one entry for each of F1–F35 and `claude:F8`
  (36 entries), each with a commit and test id or an explicit open/out-of-scope reason; a gate
  report under `docs/campaigns/phase6/` records the re-run reproductions of every closed finding and ends with
  the verdict `APPROVE`.

## Test failure allowlist

These existing tests encode a defect the phase fixes. Each is either adapted by a fixture that
configures the grant it needs, its assertions unchanged, or has the assertions that encoded the
defect replaced, as AC38 allows; either way it carries a docstring citing its finding and
requirement, the pair its entry names. None is skipped, xfailed, deleted or loosened.

- `tests/test_twitch.py::test_chat_send_uses_configured_identity_and_preserves_event` — asserts an
  ungranted compatibility send reaches Helix, which R1 makes a refusal (F1 and R1).
- `tests/test_twitch.py::test_send_failure_continues_chain_and_next_send_succeeds` — same.
- `tests/test_twitch.py::test_pending_send_releases_response_on_timeout_cancellation_and_close` — same.
- `tests/test_twitch.py::test_notification_mapping_dedup_and_retry_reconnect` — sends through the
  ungranted compatibility route after reconnect, which R1 makes a refusal (F1 and R1).
- `tests/test_twitch.py::test_lost_sent_trace_neither_retries_nor_repeats_the_send` — compatibility
  parametrization sends without a grant, which R1 makes a refusal (F1 and R1).
- `tests/test_twitch.py::test_confirmed_outcome_never_waits_for_the_sent_trace` — same.
- `tests/test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` — greps
  the guide text for command names, which passes for a command the code no longer has; its grep is
  replaced by command resolution against the code, as R8 requires (F18 and R8).
- `tests/test_automation_bridge.py::test_ac16_no_run_observation_is_a_success` — run alone it
  records no observation and so asserts nothing; that under-assertion is itself the defect being
  fixed (F28), and its assertions are replaced by ones that exercise at least 1 recorded observation
  on their own, as R9 requires. Its existing docstring (`tests/test_automation_bridge.py:2217`) is
  extended to cite F28 and R9; the replacement is proven by the mutation AC35 names — the bridge
  reporting a run of the action as `success` makes the test fail run alone, and restoring the code
  makes it pass (F28 and R9).
- `tests/test_bus.py::test_absent_or_non_finite_limit_is_rejected_naming_the_setting` — parametrized,
  it carries `assert EventBus(**_VALID_LIMITS) is not None`, which cannot fail (F32). Only that line
  is replaced, by an assertion that can fail — for example on a property of the constructed bus that
  the parameters determine — and the test's existing docstring is extended to cite F32 and R9; the parametrized
  rejection assertions around it stay unchanged, neither deleted nor loosened (F32 and R9).

## Caller enumeration

Search method: `grep -rn` over `core/`, `modules/`, `tests/` for `executor.invoke(`, `channel.chat.send`
/ `_CHAT_SEND_EVENT`, and `ActionExecutor(`. Blind spot: dynamic dispatch through bus wildcard
subscriptions is not detected by grep.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/brain/__init__.py:3879 | brain run → `ActionExecutor.invoke` | No change: a fresh `call_id` per call; a reused id now needs the same logical call (R1). |
| modules/agent_link/__init__.py:1186 | received call → `ActionExecutor.invoke` | Passes the pairing-fixed principal, not the frame's (R2). |
| modules/twitch/__init__.py:1116/1476 | `handle_chat_send` (compatibility route) | Becomes an executor invocation of `chat.write` (R1). |
| tests/fixtures/modules/fakeplatform/__init__.py, tests/test_integration.py, test_users.py, test_shutdown.py, test_examples.py, test_loader.py, test_bus.py, test_brain.py | producers/declarers of `channel.chat.send` | Only tests sending to Twitch through it need a grant; manifest declarations unchanged. |
| shipped `*.example` profiles | none publish `channel.chat.send` (brain uses the executor) | No profile change (constraint 8). |
