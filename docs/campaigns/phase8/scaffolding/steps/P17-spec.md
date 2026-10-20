# Step P17 — Full-branch review gate (all of R1–R9; AC1–AC40)

Plan step `P17` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Before PR delivery, review the whole branch diff `7782e5f..HEAD` as one change,
  not commit by commit. Per-commit reviews miss cross-file bugs by construction: each step can be
  correct in isolation yet broken when its files interact — e.g. P6's `draws` shape consumed by the
  traces of P8/P9/P11/P13, P7's `evaluate_async` refactor against the sync path every shipped input
  still calls, the vocabulary of P2 against the ownership guard that P12 now exercises with a
  producing `audio_input`, the hook supply of P7 against P12's `prepare`, and the counts adapted in
  two steps (P9, P11), and P1's guard against every fake P15a–P15e introduce (a fake that opens a
  socket would only show up when the whole suite runs). The review checks: every requirement and AC mapped to a passing test; the
  allowlist only adapted (diff of each allowlisted test shows constant changes only); no
  `*.example` profile edited; `dependencies` unchanged; no module imports `core.runtime/loader/
  lifecycle` at module level in `modules/operator`; no token/header/audio in any trace; the
  flagged decisions D1–D8; the AC33 conversions of P15a–P15e test by test. For each
  converted node, the old and new `assert` statements are compared: none is removed or loosened, and
  only the transport or fake name changes (shortcut 1, relaxed tests). No converted test mocks its
  own subject (shortcut 10, pass-by-mock). The fake that replaces a real process or socket is a
  *dependency* double. The gate also confirms that the guard has no exemption left, and it checks
  the 11 fake-done shortcuts. Findings are fixed in a
  fix round before delivery; the record is written to `full-branch-review.md`.

## Requirements

Plan mapping: R1, R9
Acceptance criteria owned by this step: AC1, AC40
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

[`docs/campaigns/phase8/full-branch-review.md`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P16]

All dependencies are already merged into `main` when this step runs.

## Tests

Full suite green; P1's AC16 comparison green on the final tree; `tests/test_triggers.py`
  byte-identical to `7782e5f` (`git diff --exit-code 7782e5f -- tests/test_triggers.py`).

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

A gate that only re-reads step reports repeats per-commit blindness; it must read the
  integrated diff and run the suite on the final tree.

## Caller enumeration

Search: `grep -rn` over `core/`, `modules/`, `tests/` for `TriggerDecision(`, `TriggerTypeDeclaration(`,
`TriggerSpec(`, `TriggerEngine(`, `.register(`, `.evaluate(`, `fields(TriggerDecision)`. Blind spots:
fixture manifests under `tests/fixtures/modules` reach the loader without a call site; third-party
modules outside the repo (none known) would see only additive optional fields.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/triggers.py | `BUILTIN_TRIGGER_TYPES` (4 `TriggerTypeDeclaration(...)`) | No change: new fields optional, absent on generic types (P3). |
| core/triggers.py | engine's own `TriggerDecision(...)` constructions | `draws` defaults to `()`; declared probability rules pass entries (P6). |
| core/runtime.py:1458 | `_OwnedTriggers.evaluate` builds `TriggerDecision(... REASON_INPUT_NOT_OWNED)` | Unchanged (`draws` default). Gains `evaluate_async`/`supply_hook` forwarders (P7). |
| core/runtime.py:1664 | `ModuleContext` | Gains `supply_trigger_hook` (new, 0 existing callers) (P7). |
| core/loader.py:2107 | `_manifest_triggers` builds `TriggerTypeDeclaration`, `TriggerSpec` | Passes the new keys and `fields` (P4). |
| core/loader.py:1366 | `registry.register(name, declaration.triggers, companion_name=...)` | Signature unchanged; accepts declared types (P5). |
| core/main.py:1295 | `TriggerEngine(...)` | Unchanged: `sleeper` optional and derived (D3). |
| modules/twitch/__init__.py:2426, kick:1626, youtube:1836, streamerbot:1987, watch:707 | `engine.evaluate(event, ...)` | Unchanged sync API; twitch's reward events use the same call (P8). |
| modules/operator, modules/timer (new) | `context.triggers.evaluate` | New callers of the sync API (P9, P11). |
| modules/audio_input (P13) | `context.triggers.evaluate_async`, `context.supply_trigger_hook` | New callers of the new APIs. |
| tests/test_watch.py | `TriggerDecision(...)` | Unchanged: `draws` default. |
| tests/test_triggers.py:819 | `fields(TriggerDecision)` subset assertion | Still holds (`<=`); file not edited (AC10). |
| tests/test_triggers.py, tests/test_twitch.py | `TriggerTypeDeclaration(...)`, `TriggerSpec(...)` | Unchanged: keyword fields keep their meaning. |
| modules/kick, youtube, streamerbot, watch `_trace_decision` | build their own six-key trace payload | Unchanged; they host no declared probability type (D6). |

## Requirement coverage

| Requirement | Steps |
|-------------|-------|
| R1 | P3, P4, P5 |
| R2 | P5, P6, P7 (admission, AC9) |
| R3 | P7, P12 (shipped hook) |
| R4 | P1, P2, P16/P17 (AC16 final) |
| R5 | P8 |
| R6 | P9, P10 |
| R7 | P12, P13, P14 |
| R8 | P11 |
| R9 | P1 (whole-suite guard and ledger), P15, P15a–P15e (AC33 conversions), P16 (AC33 proof), P17 |

## Ordering rationale

P1 comes first because AC16's phase-7 baseline is only truthful when captured before any production
change. P2 appends the vocabulary, which P3's `kinds` validation, the brain routes and every new
input need. The kernel is then built bottom-up along its own data flow: contract shapes (P3) before
the loader that constructs them (P4), registry acceptance and policy validation (P5) before the
engine can evaluate a declared rule (P6), and the synchronous declared path (P6) before the async
hooked path that refactors around it (P7). Every family depends on the finished kernel. Twitch (P8)
comes first because it is the smallest family and exercises declared rules in an existing input.
The operator (P9, then its press command P10, which needs P9's request core) comes before the timer
(P11) so the allowlisted module counts move 19→20→21 in order. P11 also depends on P10 so those
count constants are edited in a fixed sequence. The audio input comes last (P12 → P13 → P14). It
uses the most kernel surface (hooks, `evaluate_async`, the input role), and its three parts build on
each other: settings and hook, then the one-way pipeline, then the lifecycle and backoff that drive
the pipeline. Documentation (P15) is written once every behaviour it documents exists, and its
examples are loaded against the final manifests. The AC33 conversions (P15a–P15e) come after P15:
several of them edit files the feature steps also edit (`test_audio_input.py`, `test_config_ui.py`,
`test_profiles.py`, `test_input_ownership.py`), and running them last keeps each file's edits in one
order. P1 installs the guard first, so every test the phase adds is isolated from its first commit,
and the ledger keeps the 166 pre-existing nodes green until they are converted. The conversions are
chained: P15a, then P15b, P15c, P15d and P15e. Most of them extend `tests/conftest.py` and shrink the
same ledger, and P15e closes it. P16 proves AC33 on the whole suite, so it needs P15e. Twitch
(P8) precedes the operator (P9) by an explicit dependency, because both edit
`test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities`. The summary (P16)
needs the step commits. The
full-branch gate (P17) runs last, on the integrated tree.

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
