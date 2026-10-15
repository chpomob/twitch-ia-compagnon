# Step P15F1 — the executor carries a provider's cause when it takes over an emitted call (deviation D11)

Fix step created during the Phase 5 campaign. It is **not** one of the plan's numbered steps: it
exists because step P15 stopped before editing anything, exactly as its instructions require, and
reported that the kernel loses the reason an uncertain outcome is uncertain on the paths the executor
owns. Its full report is preserved at
`docs/campaigns/phase5/scaffolding/loop-out/phase5-p15/01_build.json`.

Authority documents (read them, do not re-derive):
- Approved plan: `docs/campaigns/phase5/plan.md` (deviation D8 defines the transmission boundary)
- Approved specification: `docs/campaigns/phase5/spec.md` (R4, AC16, AC17)
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The record of this amendment and its rationale: `docs/campaigns/phase5/scaffolding/plan-amendments.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules).
Working tree at `main`.

## Problem

D8 requires that a call cancelled after its frame was sent be answered with a **provider-authored**
interruption record carrying `cause: acknowledgement_lost`, and AC16 asserts the cause **exactly**.
The kernel does not carry it on the paths the executor owns.

Reading `core/actions.py`:

- `_terminate_uncertain` (the executor's own interruption record: outer cancellation, or its own
  deadline) calls `_terminate(..., code=code, message=..., invoked=True)` — the message is the
  executor's generic "was cancelled with emission …", and **no cause** is passed.
- `_terminate` builds `error = {"code": code, "message": message, "retryable": False}` — the error
  mapping has no `cause` member at all.
- The conversion path (`_reject`, step 4c of `_validate_observation`) rebuilds
  `error = {"code": ERROR_EXTERNAL_UNKNOWN, "message": "(…) and its observation was rejected (…)"}`
  from a provider-authored `timeout`/`cancelled`/`error` record, so the provider's cause and its
  "the companion does not know whether the action ran" wording are lost there too.
- `_is_interruption_record` states it plainly: it reads **status names only**.

The reporter verified this on the real `ActionExecutor` with a stub provider bound to the bridge's run
spec (provider marks not-emitted then emitted, catches the cancellation, returns a record carrying
`error.cause = "acknowledgement_lost"`). All four paths — outer cancellation with either record shape,
and the executor's own deadline with either record shape — end as `external_unknown` /
`external_effect_unknown` **without the cause**, with the executor's message. A provider cannot dodge
the timer by finishing at the same instant: the executor treats `completed_at >= expires_at` as late.

**Why this is not a detail.** Status and code stay correct on every path, so a test asserting status
and code alone passes today and the defect hides. The cause and the uncertainty wording are precisely
what tells the model — and the operator's audit — that the companion does not know whether the action
ran. Hiding it behind a weaker assertion is the class of shortcut the operator's brief rules out.

**It is also latent in shipped modules.** `modules/clips` publishes `external_unknown` with cause
`confirmation_lost` and documents it in two places; `modules/stream_control` does the same. Their
tests assert the cause only on the paths where the *provider* answers (where the record is adopted as
written), so the executor-owned paths have never been covered. Fixing the executor makes their
already-published causes visible where they were being dropped.

## Files

[`core/actions.py`, `tests/test_actions.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of
editing it.

## Dependencies

[P11]

All dependencies are already merged into `main` when this step runs.

## Behaviour to implement

1. **Conversion keeps the cause.** When the executor converts a provider-authored interruption record
   (`timeout`, `cancelled` or `error`, as `_is_interruption_record` defines them) into
   `external_unknown` under the emission rule, carry that record's `error["cause"]` — when it has one —
   into the terminal error, and keep its message alongside the conversion note. A record without a
   cause keeps today's exact message and shape.
2. **Take-over keeps the cause.** When the executor interrupts a call itself (its caller cancels it, or
   its deadline expires) and the provider task stopped within the cancel grace **and** returned a
   record whose status resolves to `external_unknown` under the emission rule, keep that record's cause
   and message in the terminal observation. The `CancelledError` still propagates to the caller exactly
   as today: the executor does not swallow it, and the timer stays in force.
3. **Nothing else moves.** No status or code changes on any path. A provider that reports no cause
   produces byte-identical records to today, so the phase-2 R10 guarantees (a provider-authored
   interruption record at or after the deadline is adopted; a late confirmation is never a success)
   stand unchanged.

## Tests

In `tests/test_actions.py`, with the existing executor harnesses:

- each of the four paths the reporter tabulated — outer cancellation and executor deadline, each with a
  provider record shaped `cancelled` and `external_unknown` — ends with the provider's cause preserved
  and its message present, and with the same status and code as today;
- a provider-authored interruption record **without** a cause yields exactly today's record (assert the
  full error mapping, not just the status);
- a provider that ignores the cancellation and never returns is unchanged: the executor's generic
  record, no cause, `CancelledError` propagated;
- the adopted-as-written path (a provider answer inside the deadline) is unchanged for both a record
  with and without a cause;
- a regression test for a shipped module's shape: a provider publishing `external_unknown` with cause
  `confirmation_lost` (the `clips`/`stream_control` shape) now surfaces that cause on an
  executor-driven path too.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,430 passed / 18 skipped, the count when this step starts.
Do not weaken any existing assertion: if one encodes the causeless record as the expected outcome, it
encodes the defect — replace it and cite R4 of the phase 5 specification in the docstring.

## Risks

- The executor's terminal records are the observable contract of every action, and the phase-2/phase-3
  tests assert them. Run the whole suite; a change in an error mapping that is not strictly additive
  must be reported rather than absorbed.
- Keep the executor's authority intact: a provider must not be able to turn its own uncertainty into a
  success, and the timer must not become optional.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P15F1 — ...`). The scope MUST be `phase5`.
- Keep the phase-0 to phase-4 guarantees in force, in particular the single global startup/shutdown
  deadline, the default-deny executor order (authorization, arguments, invocation, result validation)
  and phase-2 R10's deadline ruling.
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do
  because this amendment did not cover it.
