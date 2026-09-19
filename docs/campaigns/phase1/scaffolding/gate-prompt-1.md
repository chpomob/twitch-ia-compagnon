# Phase 1 — full-branch review gate (P24)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`.
READ-ONLY: do not modify, stage or commit anything. You may run `git`, `grep`, `rg`, `cat` and the
test suite (`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider`).

## What you are reviewing

The COMPLETE phase-1 change set: `git diff 251f727..HEAD` is phase 0 (already gated, do not re-review
it); the phase-1 branch diff is **`git diff d2517b5..HEAD`** — review THAT, file by file, together.
Per-commit reviews cannot catch cross-file defects by construction: each step is correct in isolation
yet the set only holds when the same `run_id`, the same attachment store, the same clock and the same
cancellation ownership flow across `core/`, `modules/` and `tests/`.

## Authority documents

- Specification (requirements R1–R8, acceptance criteria AC1–AC58): `docs/campaigns/phase1/spec.md`
- Plan (24 steps, caller enumeration, ordering rationale): `docs/campaigns/phase1/plan.md`
- Design and phase-1 exit criteria: `docs/design-v2.md` (section 6, "Phase 1")

## Checklist — answer each with PASS / FAIL / NOT-VERIFIABLE and the command you ran

1. **Requirement mapping**: every R1–R8 maps to landed code, and every AC1–AC58 maps to a NAMED test
   that actually runs in the suite. List any AC with no enforcing test.
2. **Caller enumeration**: every entry in the plan's "Caller enumeration" section is migrated in the
   final tree (`grep -rn` the old constants/parameters, e.g. the removed hardcoded delivery constant).
3. **Delivery contract** (the product decision): the run's delivery is a configured ordered list
   (mode `fixed` or mode `modules` derived from enabled modules declaring a delivery capability, with a
   configurable preference order); each entry declares how the answer text maps into its arguments;
   entries run at the TERMINAL step only; the model never selects the delivery and never performs an
   intermediate effect. Verify with `grep` that NO delivery action name is hardcoded in
   `modules/brain/` logic, and that adding a delivery module requires no brain change.
4. **Phase-0 guarantees still hold**: bounded admission, phase lifecycle ordering, explicit terminal
   action outcomes, default-deny authorization INCLUDING reads, bounded retention, one absolute
   startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss diagnostics.
5. **`core/main.py` hygiene**: `grep -n "_MODULE_REQUIRED_FIELDS\|twitch\|brain\|audit" core/main.py`
   returns 0 lines; no bus attribute added during activation; terminal traces only through the
   supervision recorder, recorded before published.
6. **Honesty checks** (the fake-done classes): any weakened/skipped/xfailed assertion, any swallowed
   error around uncertain outcomes, any stub validator, any test bent to match an implementation
   instead of the approved contract, any positive-duration `sleep` in tests
   (`grep -rn "asyncio\.sleep(\s*[0-9]" tests/` must be empty). Say explicitly which you checked and how.
7. **Remote/proxy path**: the proxy protocol is the normative one in `docs/proxy-protocol.md`; one agent
   per brain; pairing token valid/invalid; reconnection with backoff; attachments by reference (never a
   local path assumed usable remotely); a drop AFTER an effect is classified by nature, never retried
   blindly; the bus is NOT distributed.
8. **Failure paths**: no-applicable-rule (reads included), forbidden/disabled actions, invalid
   arguments, provider becoming not-ready mid-run, oversized/expired image, timeout then late/double
   response, cancellation at each step, proxy break after an effect → correct accounting, no blind
   retry, no leaked tasks or attachments, and an uncertain external outcome NEVER memorized as a
   confirmed send.
9. **Deployment profiles + packaging**: the two profiles are distinct and honest; a clean-environment
   install discovers the modules/manifests; the smoke tests carry no embedded secret.
10. **Scope discipline**: nothing outside the specification's target list was changed (test fixtures and
    the plan itself excepted), and the historical v1-MVP documents were not overwritten.

## Deliverable

Write the report to `docs/campaigns/phase1/scaffolding/gate-report-1.md`:
- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **CHECKS**: the checklist above, one line each with the command used
- **NEW FINDINGS**: id, severity, `file:line`, summary, evidence, minimal required fix — each with a
  concrete counterexample or an executed reproduction; no stylistic padding
- **NOT VERIFIED**: anything you could not exercise, stated plainly rather than assumed

This gate CLOSES phase 1; it does not redesign it. If everything you can check passes, say APPROVE
plainly.
