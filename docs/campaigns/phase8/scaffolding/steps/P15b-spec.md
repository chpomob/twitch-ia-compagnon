# Step P15b — AC33 conversion

Plan step `P15b` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`tests/conftest.py` gains `FakeOSProcess`, a scripted stand-in for
  `asyncio.subprocess.Process` and `subprocess.Popen`. It has a pid, a process group, a script of
  stdout/stderr chunks and an exit code, and modes such as "ignore terminate", "never write", "leave a
  child holding stdout" and "flood stdout". It records every `send_signal`/`terminate`/`kill`/`os.killpg`
  addressed to it and the `start_new_session`/`env`/`shell` options it was given. A `fake_spawn`
  factory returns one per call. These tests are converted to it, each through the seam its subject
  already has:
  - `test_audio_input` (4 `test_the_shipped_runner_*` nodes) through `_subprocess_runner`. The
    shipped `RecorderProcess` wrapper still runs; only `asyncio.create_subprocess_exec` beneath it is
    the fake, and `os.killpg` is monkeypatched to route to the fake's group.
  - `test_audio_output::test_the_subprocess_runner_feeds_a_real_player_its_stdin` through `spawn`.
  - `test_capture` (5 `run_command`/`command_source` nodes and the shell-script background-capture
    node) through `spawn`/`_subprocess_runner`.
  - `test_config_ui` (the 15 `ac30`/`ac31`/`ac32`/`ac35`/`ac39_ac40`/`d9`/child-environment nodes)
    through `Supervisor(popen=...)`. The fake child publishes the status record the shipped child would
    write, which `_apply_ui`'s `body` scripts already describe. Each body becomes a fake script.
  - `test_shutdown` (the two `test_cli_*` nodes) now calls `core.main.main(argv)` in-process. They
    assert the same deadline and exit code, and that no non-daemon thread is left
    (`threading.enumerate()`), which stands in for "the process exits".
  The `mkfifo`/`os.open` helpers that only fed real children are removed together with the child
  scripts. Converted nodes leave `AC33_PENDING`.

## Requirements

Plan mapping: R9
Acceptance criteria owned by this step: AC33, AC33
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

[`tests/conftest.py`, `tests/test_audio_input.py`, `tests/test_audio_output.py`, `tests/test_capture.py`, `tests/test_config_ui.py`, `tests/test_shutdown.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P15a]

All dependencies are already merged into `main` when this step runs.

## Tests

Each converted node passes under the guard with its assertions kept. These include the
  process-group kill reaching the child, the kill at the deadline, bounded stdout, `shell=False` with
  the literal argv `;echo x`, `$HOME`, `a b`, and an inherited environment plus the status variable.
  New negative checks prove the fake is not trivially green: a runner that forgets `killpg` fails the
  wrapper test, and a supervisor that waits without a bound fails the ignore-terminate test on the
  ManualClock.

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

This step loses the most fidelity. A fake process cannot prove that the OS delivers a
  group kill, so `summary.md` (P16) names these nodes as "fake-OS only" (D8). A fake that is too
  obliging would make the runners look correct, which is why the negative checks above exist. Signal
  and reap ordering must be scripted from what the runner actually calls, not from what the old
  child did.

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
