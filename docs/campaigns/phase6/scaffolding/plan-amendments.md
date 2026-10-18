# Phase 6 — amendments to the approved plan and specification (gate record)

This file exists so that a change the phase made to an existing test outside the specification's
failure allowlist is **visible in the durable record** instead of living only in a diff. It is the
record phase 5 used (`docs/campaigns/phase5/scaffolding/plan-amendments.md`). The closing gate's
second pass must read it, and the phase summary must carry it.

The gate's first pass (`docs/campaigns/phase6/full-branch-review.md`, finding **N3**) found, by an
assertion-AST comparison against the pre-fix tree `8010edc`, five existing tests outside the
allowlist whose assertions changed. It judged them sensible adaptations but refused the *silence*:
the binding allowlist (nine authorised replacements) does not name them. This file resolves that
authority conflict explicitly, one entry per test. Step P20F5 records it; it does not widen the
allowlist.

The rule every entry is held to (AC38, standing phase-6 decision): outside the allowlist an assertion
may only be **extended** — every predicate the pre-fix test checked still holds, new predicates are
added, and no count moves without a behavioural reason the phase's own requirements impose.

## N3.1 — `tests/test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant`

**What changed (P11, `374d88d`).** The exact equality over the brain manifest's
`settings_schema.properties` lists one more member, `max_response_bytes`; a new assertion checks that
property is an `integer` with `minimum: 1`. The docstring names Phase 6 R6 (F12).

**Why the phase forced it.** R6 / AC19 bound the model's response in bytes before JSON decoding
(F12). The bound is a setting, so `modules/brain/module.yaml` declares `max_response_bytes`. The test
asserts the property set by **equality** — its purpose is that no undeclared setting slips in — so
an exact assertion over a manifest that now declares the property must list it, or it fails on the
phase's own requirement.

**Evidence it is an extension.**
- Every pre-fix predicate still holds: `schema["type"] == "object"`; `required` is still exactly
  `VALID_SETTINGS` (unchanged line — the new property is optional, no shipped profile gains a
  required key); every pre-fix property (`VALID_SETTINGS | {"persona", "routes"}`) is still a member;
  the per-group limit checks after it are untouched.
- The equality stays exact (it was not relaxed to `<=`): an extra undeclared property still fails.
- Added: the new property's type and lower bound.
- No count involved.

## N3.2 — `tests/test_proxy.py::test_agent_link_manifest_declares_the_input_role_no_event_and_no_action`

**What changed (P3, `986bf92`).** The exact equality over the agent_link manifest's
`settings_schema.properties` lists three more members: `principal`, `max_running_calls`,
`max_duplicate_waiters`, with a comment naming Phase 6 R2.

**Why the phase forced it.** R2 bounds a paired peer by the pairing: the principal is fixed by the
agent's configuration and never read from the frame (F2, AC7), and running executions and
duplicate-call waiters are bounded (F14, AC9). Each is a setting `modules/agent_link/module.yaml`
now declares (lines 152–168). The exact-set assertion must name them.

**Evidence it is an extension.**
- Every pre-fix member (`brain_url` … `limits`, eleven names) is still listed, on the unchanged
  lines; the assertion is still `==`, so any undeclared property still fails.
- The three new settings are optional; the test's `required`, role, event and action assertions are
  unchanged lines.
- No count involved.

## N3.3 — `tests/test_proxy.py::test_agent_link_validate_settings_accepts_loopback_ws_and_wss_anywhere`

**What changed (P3, `986bf92`).** The `full` settings mapping gains the three R2 settings; three
assertions are added on the parsed result: `parsed.principal == PRINCIPAL ==
agent_link.DEFAULT_PRINCIPAL` and the two bounds equal their module defaults.

**Why the phase forced it.** The test's purpose is that a mapping carrying *every* declared setting
validates with zero diagnostics. After R2 (F2, F14) a mapping without the three new keys is no longer
"every declared setting"; adding them keeps the test's meaning. The parse assertions prove the new
settings reach the parsed configuration rather than being merely tolerated.

**Evidence it is an extension.**
- `agent_link.validate_settings(full) == []` is unchanged and still exact.
- Every pre-fix parse assertion (URL, reconnect, heartbeat, call table, `max_frame_bytes`,
  `action_seconds`, …) is an unchanged line.
- The diff adds input keys and three assertions; it removes none.
- No count involved.

## N3.4 — `tests/test_proxy.py::test_agent_link_validate_settings_refuses_bad_entries_and_non_finite_bounds`

**What changed (P3, `986bf92`).** The bad mapping gains three bad entries (`principal: ""`,
`max_running_calls: inf`, `max_duplicate_waiters: 0`) and the exact expected set of refused field
names gains `'principal'`, `'max_running_calls'`, `'max_duplicate_waiters'`.

**Why the phase forced it.** The test proves that each declared setting refuses a bad value and that
non-finite bounds are refused. R2's new settings (F2, F14) are declared settings with bounds; an
exact assertion over the refused fields that omitted them would leave the new bounds untested.

**Evidence it is an extension.**
- Every pre-fix bad entry is still in the mapping, and every pre-fix refused field name (`'brain_url'`
  … `'_connector'`) is still in the expected set, on unchanged lines.
- The equality stays exact: a bad entry that slipped through, or an unexpected diagnostic, still
  fails. The diagnostic-prefix assertion and the non-mapping refusal are unchanged lines.
- The expected set grows by exactly the three fields whose bad values were added — one-for-one, so the
  growth is the new input, not a relaxation.

## N3.5 — `tests/test_youtube.py::test_ac39_three_platforms_on_one_runtime`

**What changed (P10, `0c3e0f6`).** The YouTube fake's script is preceded by `PRIMING_PAGE` (an empty
first page); after the Kick webhook settles the test advances the clock by one poll interval
(`POLL_SECONDS`) and asserts `api.counts["list"] == 2` where the pre-fix test asserted `== 1`.

**Why the phase forced it.** R5 / F11: the first page of a freshly resolved live chat is history and
must not be admitted as new messages. A YouTube message the test wants admitted can therefore only
arrive on the **second** list. The count moves because the code now does one more list before the
message is admitted — a behavioural reason imposed by the requirement, not an adjustment to make a
number fit. The test's subject (AC39: the same viewer on Kick and YouTube lands in two sessions of one
scheduler, three disjoint `chat.write` bindings, clip/poll unbound with 0 requests) is unchanged.

**Evidence it is an extension.**
- P20F5 restores the pre-fix predicate at its pre-fix point: right after the Kick webhook settles,
  before the clock advances, the test now asserts `api.counts["list"] == 1` — the one list is the
  priming page — and `scheduler.live_sessions == 1`, proving that page admitted nothing. Only then
  does it advance one poll and assert `list == 2`.
- Every other pre-fix predicate is an unchanged line: `live_sessions == 2`, `queue_depth == 1` for
  both the Kick and the YouTube session of the shared viewer, `kick_session.requests == []`, and
  zero `insert`/`ban`/`delete` calls, plus the earlier binding and unbound assertions.
- The count was raised by exactly one, matching the one priming list F11 adds.

## Not in this record

- `tests/test_installation_doc.py::test_the_guide_prints_the_commands_the_code_still_has` is an
  allowlisted F18 replacement, not an N3 entry. Its first replacement dropped the pre-fix exact
  assertions on the guide's run and configuration-UI commands (finding **N2**). P20F5 restores them
  on top of the code resolution: on both guides `python -m core.main --config mon-compagnon.yaml`,
  `python -m core.config_ui --config mon-compagnon.yaml` and `python -m demo` must be printed, so a
  UI command altered to `--config wrong.yaml` fails the test on each guide.

## A1 — AC2 named a trace the kernel does not define (`action.requested`)

**What changed.** AC2 asked for "exactly 1 `action.requested` and 1 `action.completed` trace". The
criterion now names `action.started`. No code and no test changed: the test
`test_twitch.py::test_ac2_the_compatibility_send_is_traced_as_an_executor_call` already asserted the
`started`/`completed` pair, and the executor has always emitted it.

**Why the amendment, and not the event.** The kernel defines exactly one start trace for an action:
`TRACE_ACTION_STARTED = "action.started"` (`core/contracts.py:149`), published by
`ActionExecutor.invoke` (`core/actions.py:1361`). No `action.requested` exists anywhere in the product,
and none did before this phase. The criterion's own closing clause — "the same traces an executor call
produces" — settles which name was intended; only the literal token was wrong. Implementing an
`action.requested` event to satisfy a typo would add a second, redundant start trace to the product's
evidence trail, which is the opposite of what R8 asks for.

**Why it surfaced only at the gate.** The first pass recorded it in its R1 row as an "AC2 literal
discrepancy" without making it the reason for refusal; the second pass, with every product finding
closed, refused to treat the two names as interchangeable on its own authority. It was right not to:
an acceptance criterion is the operator's contract, and only a recorded amendment can change it.

**What the gate must check in the third pass.** That AC2 now names `action.started`, that the named test
asserts exactly that pair, and that no `action.requested` remains anywhere in the phase's documents.
