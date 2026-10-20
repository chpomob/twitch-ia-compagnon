# Step P4 — The loader parses and diagnoses the new manifest keys (R1; AC1, AC2, AC3 load half)

Plan step `P4` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`_TRIGGER_KEYS` (`core/loader.py:225`) gains `fields`; `_TRIGGER_TYPE_KEYS`
  (`:226`) gains `kinds`, `match`, `probability_parameter`, `hook`, `hook_timeout_seconds`.
  `_manifest_triggers` (`:2107`) passes them to `TriggerTypeDeclaration` and `fields` to
  `TriggerSpec`. For a declared type, every `ContractError` is re-raised as a load diagnostic whose
  field is `triggers.<input>.types[i].<sub-path>` (the input = the manifest name), through the
  existing `_field_error`/`_sanitised_reason` path so no configured value is echoed; a non-generic
  name with neither `match` nor `hook` is refused at `triggers.<input>.types[i].name`. Generic-type
  diagnostics keep their existing `triggers.types[i]` wording (pre-existing loader tests assert it).
  Load happens before any transport opens, so a refused declaration opens 0 transports. The fixture
  module `tests/fixtures/modules/declared/badge` (a minimal input that opens nothing) carries the AC1
  declaration; refusal fixtures are built in-test by writing manifests to `tmp_path`.

## Requirements

Plan mapping: R1
Acceptance criteria owned by this step: AC1, AC2, AC3
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

[`core/loader.py`, `tests/test_declared_triggers.py`, `tests/fixtures/modules/declared/badge/module.yaml`, `tests/fixtures/modules/declared/badge/__init__.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3]

All dependencies are already merged into `main` when this step runs.

## Tests

AC1 (the `badge_tier` manifest with `triggers.fields: {badge.tier: string}` loads);
  AC2 (the nine fixture declarations, each refused with 0 transports opened — asserted with a
  transport-counting fake — and a diagnostic containing `triggers.<input>.types[i]` and the
  sub-path); AC3 load half (neither match nor hook → `triggers.<input>.types[0].name`;
  non-required probability parameter → `triggers.<input>.types[0].probability_parameter`; no
  diagnostic contains the configured value). `tests/test_loader.py` passes unmodified.

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

Two diagnostic prefixes (`triggers.types[i]` for generic, `triggers.<input>.types[i]`
  for declared) is a deliberate split to keep pre-existing loader tests exact; documented in the
  guide's diagnostics section. A fixture module directory under `tests/fixtures/modules` must not be
  discovered by the shipped-manifest counts (they scan `modules/` only; verify).

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
