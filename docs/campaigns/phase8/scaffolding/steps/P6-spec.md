# Step P6 — Declared-rule evaluation, the wildcard matcher, probability draws and `draws` (R2; AC4, AC5, AC7, AC8, AC10, AC35, AC36)

Plan step `P6` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- `_evaluate_rule` (`core/triggers.py:1155`) dispatches a declared type to a new
    `_evaluate_declared(declaration, parameters, normalized, rng, counter)` which: returns
    `<type>_kind_mismatch` when the kind is outside `kinds`; evaluates the frozen tree iteratively over
    the normalized payload — scalar comparisons false on an absent field or a JSON-type mismatch
    (`bool` is never a number), `in` false when the left value is absent or not a scalar, else true iff
    the list holds a same-type equal element (strings case-sensitive), a comparison whose `param`
    operand is absent is true, `equals` case-sensitive, `matches` per D5; then, when a
    `probability_parameter` is declared, draws exactly one value whether or not the tree matched
    (`<type>_probability_miss` when tree matched and draw ≥ probability). Reasons are `<type>_match`,
    `<type>_no_match`, `<type>_kind_mismatch`, `<type>_probability_miss`, bounded by
    `_bounded_reason`. The normalized view (`_Normalized`, `:612`) exposes the payload read-only for
    `field` paths; no module code runs, no transport opens.
  - `_wildcard_match(pattern, text) -> (bool, steps)` per D5; the engine adds `steps` to
    `counts["wildcard_steps"]`.
  - `TriggerDecision` (`:366`) gains `draws: tuple[Mapping[str, Any], ...] = ()` — its only shape
    change — validated in `__post_init__` to entries of exactly the five keys `rule`, `parameter`,
    `probability`, `draw`, `ordinal` with scalar values. The engine counts values served by its random
    source (`ordinal` = count before the draw, per engine; an `rng` override counts from its own
    zero) and appends one entry per evaluated declared probability rule, in policy order. The
    dedup window stores the decision object, so a redelivery returns the same `draws` and draws
    nothing (`:858`). The generic `probability` evaluator (`:1192`) records nothing, so generic
    decisions keep `draws == ()`.
  - `decision_trace_payload(decision)` per D6.

## Requirements

Plan mapping: R2
Acceptance criteria owned by this step: AC4, AC5, AC7, AC8, AC10, AC35, AC36
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

[`core/triggers.py`, `tests/test_declared_triggers.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P5]

All dependencies are already merged into `main` when this step runs.

## Tests

AC4 (gold/silver/absent/`7`, kind `cue`, two-tier list, `Gold`); AC5 (self-authored,
  internal trace, non-schema-2 rejected by their existing reasons with `rule_evaluations` delta 0; a
  redelivery: 0 evaluations, 0 draws); AC7 (100 events, 50/50, exactly 100 draws; replay identical;
  `probability_miss`); AC8 (`hi*bot?` matches `Hi there BOTs`, not `hi bot`; 128 `*` over 10 000
  characters decides with no timeout path); AC35 (values `0.9, 0.1, 0.42`, decisions and the third
  `draws` literal; re-seeded replay equal; redelivery same `draws` 0 draws; generic-only incl.
  `probability` → `()`; no entry carries payload/text/other parameter); AC36 (the 63×`*a`+`*b`
  pattern over 5 000 and 10 000 `a`: no match, steps ≤ (n+1)×129, steps(10 000) ≤ 2×steps(5 000)+129,
  `counts["wildcard_steps"]` delta equals reported steps); a unit table for the matcher (empty
  pattern, `*` only, `?` at ends, Unicode case folding of `ß`/`İ` keeps the step bound). AC10:
  `tests/test_triggers.py` unmodified passes.

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

Short-circuiting tree evaluation is allowed (only the *draw* must be unconditional);
  the draw must be taken after the tree and before the combination to keep "one draw per evaluation".
  Treating Python `True` as integer 1 in `gte`/`equals` would violate the JSON-type rule — explicit
  `type(x) is bool` checks. The greedy-with-backtrack wildcard algorithm is O(n·m) and would pass the
  bound but not AC36's linear growth if implemented recursively; D5 avoids both.

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
