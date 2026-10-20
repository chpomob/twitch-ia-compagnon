# Step P7 — Hooked types

Plan step `P7` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- `TriggerEngine.__init__` (`core/triggers.py:680`) gains `sleeper` per D3.
    `TriggerEngine.supply_hook(module, type_name, hook)` and the registry's slot table per D2/D4:
    refused (`ContractError`, value-free) unless `type_name` is a hooked type in `module`'s own spec
    and `hook` is a coroutine function.
  - `async def evaluate_async(event, *, context, policy, clock, rng)` per D1: shares the invariant,
    override-validation, dedup and resolution code with `evaluate` (refactored into private helpers,
    no behaviour change); for each rule in policy order a hooked rule is evaluated only when the
    event's `metadata.source` is the declaring input and its kind is in `kinds` (else
    `kind_mismatch`/not eligible, 0 invocations); the hook receives a read-only
    (`MappingProxyType`, deep-frozen) view of the event and the rule's parameters, invoked at most
    once, raced against `sleeper(hook_timeout_seconds)`; outcomes `True` → match, `False` → no match,
    any other value or exception → `<type>_hook_error`, timeout → `<type>_hook_timeout` (the hook
    task is cancelled and its eventual result ignored), no hook → `<type>_hook_unavailable`. After all
    rules are evaluated and **before `_combine`** (`:1268`), the first failed hook outcome in policy
    order rejects the decision with its reason, whatever the combination or whether the policy is the
    default (R3 veto). The decision is recorded in the dedup window and counted like any other; a
    hook failure never raises out of `evaluate_async`.
  - `core/runtime.py`: `_OwnedTriggers` (`:1434`) forwards `evaluate_async` through the same
    ownership guard as `evaluate` and forwards `supply_hook` bound to its module; `ModuleContext`
    (`:1664`) gains `supply_trigger_hook(type_name, hook)` calling the engine with the context's own
    module name (D4). No admission, budget or authorization code is touched.

## Requirements

Plan mapping: R3, R2
Acceptance criteria owned by this step: AC9, AC11, AC12, AC13, AC14, AC34
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-8 decisions that override any contrary reading of a requirement:
- **A module declares its trigger types; the kernel evaluates them.** The four generic types
  (`probability`, `audience`, `keyword`, `event_kind`) stay and remain usable by any input. A declared
  type carries a declarative match rule; no module code runs in the hot path for it.
- **A declared type narrows, never widens, and grants nothing.** Configuration is validated against the
  module's declared schema *and* the kernel's canonical one. A type cannot assert a field its input
  does not emit, and a trigger only decides candidacy: admission, budgets and DEFAULT-DENY are
  untouched and still decide everything after it.
- **The one hook is bounded and fail-closed.** It runs only on events its own module emitted, under a
  hard timeout; raising or exceeding the bound means *no trigger*, never a silent pass, and its outcome
  is recorded like every other decision.
- **Event kinds are appended, never reordered.** `reward` and `manual` join the closed vocabulary at the
  end, the way `cue` did; earlier indices keep their order and meaning.
- **No new runtime dependency.** `aiohttp` and `PyYAML` remain the only ones. The wake-word detector is
  configured (a command or an HTTP endpoint), exactly like an audio source or the transcription
  endpoint; a detector is never embedded and never assumed.
- **Streamer.bot stays optional.** Its `cue` path is unchanged, and nothing in this phase needs it.
- **No continuous listening.** Audio publishes an event only when a trigger or a run asked for it; a
  run that nobody requested must not record audio, and continuous transcription is out of scope.
- **No test opens a socket.** Twitch, the detector, the surfaces and the audio device are faked; the
  real-instance trial stays a documented manual procedure with its own record.
- Close exactly the requirement(s) this step names. If a sibling path shows the same defect, report
  it in your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. The full suite is green at every step:
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (never the system python: no aiohttp).

## Files

[`core/triggers.py`, `core/runtime.py`, `tests/test_declared_triggers.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P6]

All dependencies are already merged into `main` when this step runs.

## Tests

AC11 (`True`, `False`, `1`, `"yes"`, `None`, raise → match, no_match, 4× hook_error;
  each counted and retained in the window); AC12 (0.5 s bound, never-returning hook →
  `hook_timeout` after `ManualClock.advance(0.5)`, a following event evaluated normally; 0 and 10.5
  refused at load — reuses P3/P4); AC13 (0 invocations for an invariant-rejected event, a dedup
  redelivery, an out-of-kinds event, another input's event; 1 for an eligible one); AC14 (a second
  fixture module supplying the first module's hooked type is refused; that type then decides
  `hook_unavailable`, same with no hook); AC34 (`none_of` with raise/timeout/no hook;
  `any_of[keyword{hi}, <t>]` with text `hi` and a raising hook → `hook_error`, same as default policy;
  0 admissions); AC9 (an accepted declared decision with a full fixture `AdmissionScheduler` is not
  admitted with the scheduler's own refusal; with a granting-rule-free configuration the run is
  offered 0 writes, read from the existing authorization view); synchronous `evaluate` on a hooked
  policy decides `hook_unavailable` without calling the hook (D1).

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

Cancelling a timed-out hook task must not leak an "exception was never retrieved"
  warning — the task's result is consumed in a done-callback. Refactoring `evaluate` into shared
  helpers risks changing generic behaviour; AC10 and P1's AC16 comparison run in this step. Ordering
  on ManualClock: the hook race uses the same sleeper the test advances, so an advance of exactly the
  bound must fire the timeout (no epsilon).

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase8): ...`, `fix(phase8): ...`, `test(phase8): ...`, `refactor(phase8): ...`, `docs(phase8): ...`).
  The scope MUST be `phase8`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase8)`.
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
