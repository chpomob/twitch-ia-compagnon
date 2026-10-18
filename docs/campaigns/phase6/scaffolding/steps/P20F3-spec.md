# Step P20F3 — peer-supplied strings never reach the executor's supervision payloads

Gate fix step of Phase 6. It answers the closing gate's **first pass**
(`docs/campaigns/phase6/full-branch-review.md`), which refused approval with 29 findings closed and five
security-boundary findings open.

## The finding (verbatim)

- **F5 OPEN** — `test_proxy.py::test_p6_ac8_peer_strings_never_enter_diagnostics_or_supervision_records` passes for error frames (token and 4 KiB); baseline **2 failed**. Probe B instead places the pairing token in a valid call's `run_id`: both **`action.started` and `action.completed` supervision records contain `pairing-secret-token` verbatim**.

### B — peer text survives in supervision (F5)

On another valid call in the same real-agent harness:

```python
await brain.call('gate-token', 2, run_id=t.TOKEN)
await brain.observation('gate-token')
leaks = [e for e in host.bus.list_events()
         if t.TOKEN in json.dumps(e, default=str)]
```

Observed two records:

```text
action.started:   call_id=gate-token, run_id=pairing-secret-token
action.completed: call_id=gate-token, run_id=pairing-secret-token, status=success
```

These are executor supervision emissions originating from a received peer frame. Fixing only `_on_error_frame` does not satisfy R2's prohibition on any peer free text in diagnostics/supervision. No token was printed from an actual configuration; this is the test harness's fixed token.

## Required resolution

Probe B: a valid call whose `run_id` is the pairing token produces `action.started` and
`action.completed` carrying `run_id=pairing-secret-token` verbatim. The text comes from the peer's frame,
not from the agent's own vocabulary.

Required: no string received from the peer is copied into a supervision record, a diagnostic, or an
`ActionCall` field that supervision publishes. The agent either drops such fields or maps them to
locally-defined values before constructing the call; the executor publishes only what the agent
constructed. Keep the call's own identity (call_id, action, destination) intact — those are validated
and normalised, not free text. AC8's error-frame cases stay green, and the general rule is proven for a
FIELD that is not an error code.

## Files

[`modules/agent_link/__init__.py`, `core/actions.py`, `tests/test_proxy.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing it.

## Tests

Probe B becomes a regression: a valid call carrying the token in `run_id` (and, separately, in
any other frame field the agent forwards) produces supervision records and diagnostics in which the token
does not occur as a substring. The call itself still executes and its result is returned. The existing
AC8 error-frame tests stay green, and the executor's own payloads for a locally-built call are unchanged
(a control proving the fix did not blank the trace).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Never the system python: it lacks aiohttp. The suite must not drop below 3,849 passed / 26 skipped.

## Authority

- The closing gate's first pass, whose findings below are quoted verbatim with their executed probes:
  `docs/campaigns/phase6/full-branch-review.md` (authoritative for this step).
- The original review: `docs/reviews/2026-10-16-full-adversarial-review.md`.
- Brief, specification, plan: `docs/campaigns/phase6/brief.md`, `spec.md`, `plan.md`.
- The phase's amendment record: `docs/campaigns/phase6/scaffolding/plan-amendments.md`.
- The pre-fix tree is `8010edc`; the phase head is the current `main`.
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon`. Working tree at `main`.

## Standing phase-6 decisions

- Phase-5 decisions stay binding: optional bridge, replacement not parallel, honest outcomes,
  default-deny, single-owner inputs, the bridge "not delivered".
- Close exactly the finding(s) this step names. If a sibling path shows the same defect, report it in
  your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. Assertions may be replaced only for a test named in
  the specification's failure allowlist, with a docstring citing its finding and requirement; elsewhere
  an assertion may only be EXTENDED (a new check on top), never reduced.
- The full suite is green at every step: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit
  (`fix(phase6): P20Fn — ...`). The scope MUST be `phase6`.
- Never weaken, skip, xfail or delete an assertion. If an existing assertion encodes the defect, replace
  it only as the specification allows and cite finding + requirement in the docstring.
- No new runtime dependency; no vendor or model name in code or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do.
- Dependencies: P20F1..P20Fn land in order; each spec assumes the previous ones are on `main`.
