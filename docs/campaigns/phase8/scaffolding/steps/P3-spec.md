# Step P3 — Declared-type contract shapes and their structural validation (R1; AC2 structural rows, AC8 pattern length)

Plan step `P3` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- `TriggerTypeDeclaration` (`core/contracts.py:1908`) gains optional fields, all defaulting to the
    generic shape so the four `BUILTIN_TRIGGER_TYPES` construct unchanged: `kinds: tuple[str, ...] =
    ()`, `match: Mapping | None = None`, `probability_parameter: str | None = None`, `hook: bool =
    False`, `hook_timeout_seconds: float | None = None`, and a derived `declared` property (true when
    any of `kinds`/`match`/`hook` is set). `__post_init__` validates a declared type: name matches
    `[a-z][a-z0-9_]*`, ≤ 32 characters, not one of the four generic names; `kinds` non-empty and each
    in `EVENT_KINDS`; exactly one of `match` / `hook: true`; `hook_timeout_seconds` in (0, 10] iff
    `hook`; the match tree is a closed node set `all`, `any`, `not`, `equals`, `in`, `gte`, `lte`,
    `matches` with operand leaves `{field}`, `{param}` or a literal (string, number, boolean, list ≤
    64 scalars), depth ≤ 8, ≤ 64 nodes; a `matches` literal pattern ≤ 128 characters; every `param`
    path resolves through declared `properties` (object segments only); a non-required parameter (or
    one under a non-required object) is refused under `not`; `probability_parameter` names a
    top-level **required** property whose schema is a number bounded within [0, 1]. Each
    `ContractError` names the sub-path (`kinds[0]`, `match.all[1].in[0].field`, `name`,
    `probability_parameter`, `hook_timeout_seconds`) and echoes no value.
  - `TriggerSpec` (`:2016`) gains `fields: Mapping[str, str] = {}` (dotted payload path → scalar type
    `string`/`integer`/`number`/`boolean`) and checks every declared type's `field` operands against
    the canonical normalized fields (`kind`, `text`, `platform`, `channel_id`, `message_id`,
    `author.id`, `author.display_name`) plus `fields`. The match tree is frozen (`_frozen_mapping`) so
    it cannot be mutated after load.
  - These are new optional constructor fields; see the caller table.

## Requirements

Plan mapping: R1
Acceptance criteria owned by this step: AC2, AC8
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

[`core/contracts.py`, `tests/test_declared_triggers.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2]

All dependencies are already merged into `main` when this step runs.

## Tests

Construction-level versions of the nine AC2 rows (name `keyword`, 33-char name, empty
  kinds, kind `banana`, both match and hook, operator `regex`, field `secret.token`, unknown param,
  depth 9), a 65-element literal list, 65 nodes, a 141-character `matches` pattern (AC8), a
  `{param: window.nope}` path (AC30's refusal), a non-required probability parameter, a
  `hook_timeout_seconds` of 0 and 10.5 (AC12); each error names the sub-path and contains no
  configured string. The AC1 `badge_tier` declaration constructs.

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

A default added in the wrong place could make a generic declaration count as declared
  and route it to the new evaluator; the `declared` flag is computed from the three discriminating
  fields only. Depth/node counting on a malicious tree must itself be bounded (iterative walk with an
  early stop at 65 nodes).

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
