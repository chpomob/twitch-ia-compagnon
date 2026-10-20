# Step P16 — Phase summary, trial-record template, whole-suite run (R9; AC32 evidence half, AC33)

Plan step `P16` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`trial-record.md` with `Status: not run` and the fields the trial procedure fills
  (date, versions, per family: what was triggered, decision trace excerpt, outcome). `summary.md`:
  per requirement R1–R9 the commit (short hash of the step that carried it) and the test identifier;
  per family what can and still cannot be triggered (no fulfil/refund, no continuous listening, no
  privileged `manual`/`timer`, hotkey binding is an OS step). Run the full suite once under the
  guard. Record the pass count, the D8 conversion list (166 nodes, 24 files) and, per converted
  node, the fidelity lost ("fake-OS only", "in-process topology", "no real install") in the
  summary.

## Requirements

Plan mapping: R9
Acceptance criteria owned by this step: AC32, AC33
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

[`docs/campaigns/phase8/summary.md`, `docs/campaigns/phase8/trial-record.md`, `tests/test_declared_triggers.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P15e]

All dependencies are already merged into `main` when this step runs.

## Tests

AC32 evidence half: `trial-record.md` exists with status `not run`, and `summary.md`
  lists R1–R9 each with a commit hash pattern and a `tests/...::...` identifier that exists.
  AC33 on the **whole** suite, in `test_declared_triggers.py`:
  - `test_ac33_the_whole_suite_runs_under_the_guard` makes three assertions. `_ac33_isolation` is
    autouse and has no ledger left. Every item in `request.session.items` uses the fixture. With the
    environment the run has (CI sets no trial opt-in), the number of items that would run unpatched
    is 0.
  - `test_ac33_no_test_reaches_a_real_spawn_or_socket` fails if any file under `tests/` contains a
    call to a socket-creation or process-spawn API outside `tests/conftest.py`'s guard and fakes and
    `tests/test_ac33_isolation.py`'s `pytest.raises` blocks. It scans with `ast` for `Popen`,
    `create_subprocess_exec`, `os.system`, `TCPSite`, `TestServer` and `create_connection`.
  - The full suite passes, and the CI command `python -m pytest tests/ -q -p no:cacheprovider` is
    green.

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

Commit hashes are only known after the orchestrator commits each step; the summary is
  written last and the test checks hash *shape* plus that each named test id is collectable. The
  `ast` scan could flag a legitimate fake that merely *names* `Popen`, for example a type annotation.
  The scan looks for calls only, and the guard and the fakes are excluded by path.

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
