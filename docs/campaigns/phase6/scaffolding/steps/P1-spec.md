# Step P1 — Executor replay protection binds the whole call; concurrent duplicates coalesce; finite grace (R1; F3, F7, F29; AC3, AC4, AC5)

Plan step `P1` of the approved Phase 6 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase6/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase6/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase6/brief.md`
- The full adversarial review whose findings this phase closes: `docs/reviews/2026-10-16-full-adversarial-review.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

In `ActionExecutor` (`core/actions.py`), replace the `call_id → observation`
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

## Requirements

Plan mapping: R1
Acceptance criteria owned by this step: AC3, AC4, AC5
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-6 decisions that override any contrary reading of a requirement:
- **Every phase-5 decision stays binding.** The bridge is optional in every case; its events
  replace the basic inputs and never run beside them; an execution the instance cannot confirm is
  never reported as a success; authorization is default-deny; an input pair has exactly one owner.
- **The fix is the finding, nothing more.** Close exactly the findings this step names. If a
  sibling path shows the same defect, report it in your final message instead of widening the
  step: the plan has a step for it, and a fix that edits another step's files is refused.
- **No test is weakened.** Nothing is skipped, xfailed, deleted or loosened. A fix ships with the
  regression that fails without it: for a product defect the test fails on `8010edc`; for a defect
  in a test (F27, F28, F32) the new assertion is proven able to fail by a stated mutation of the
  code under test. Only a test named in the specification's failure allowlist may have its
  assertions replaced, and each replacement carries a docstring citing its finding and requirement.
- **Nothing changes for a shipped profile beyond the finding.** No new required setting, no new
  dependency, no changed default path -- except where the finding IS that default (the installation
  guide's endpoint value, the README command, the overlay file name).
- **The bridge stays "not delivered".** `tests/test_phase5_docs_status.py` passes unchanged.
- The full suite is green at every step: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
  (never the system python, which lacks aiohttp).

## Files

[`core/actions.py`, `tests/test_actions.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_actions.py`: AC3 — parametrized over the 5 differing components
  (principal, session, action, destination, arguments): after `c1` by `brain` completes, the reuse is
  refused, provider count unchanged, and the observation contains none of the original result keys;
  plus a case where the identical reuse is refused by a policy change (policy refuses → refused even
  if identical). AC4 — two `invoke()` of the same logical call started with `asyncio.gather` against
  a fake provider gated on an `asyncio.Event` so both await before completion: provider count == 1,
  handler run count == 1, both observations have equal status and result; plus the first caller
  cancelled while the second waits still yields a terminal observation. AC5 — `nan`, `inf`, `-inf`
  raise `ContractError` naming `cancel_grace_seconds`; `0` and `0.5` accepted. Each test is run
  against `8010edc` (fails) for AC37.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 3,124 passed / 18 skipped, the count at the start of
this phase; the seven allowlisted tests are adapted as the specification lists them, never weakened.

## Risks

Non-trivial (branching, ordering invariant). Canonicalizing arguments must be
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

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase6): ...`, `fix(phase6): ...`, `test(phase6): ...`, `refactor(phase6): ...`, `docs(phase6): ...`).
  The scope MUST be `phase6`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase6)`.
- Keep the phase-0 to phase-4 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, the phase-3 moderation/memory/watch semantics, and the phase-4 configuration UI's
  local-only, read-only-for-secrets-and-actions surface.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
