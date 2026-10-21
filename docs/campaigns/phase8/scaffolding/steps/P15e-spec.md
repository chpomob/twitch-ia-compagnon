# Step P15e — AC33 conversion

Plan step `P15e` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- `test_stream_control::test_the_default_factory_frames_the_protocol_over_a_real_loopback_socket`
    drives the default factory's framing over an `asyncio.StreamReader` fed with bytes and a
    recording `StreamWriter` transport.
  - `test_proxy::test_the_real_listener_cuts_an_oversized_binary_frame_...` feeds the raw oversized
    frame into `aiohttp`'s `WebSocketReader`, configured with the listener's shipped `max_msg_size`.
    It asserts the same cut before the module sees the frame.
  - `test_proxy_process::test_ac39_two_process_topology_over_loopback` runs the brain and agent
    runtimes from the same shipped `*.example` profiles in one event loop. They are joined by
    `MemoryWebSocketPair`, the same way `test_proxy.py` already joins them, and the test makes the
    same correlated-run and clean-shutdown assertions.
  - `test_installation_doc` (2 nodes) calls `core.main.main([...])` and `core.config_ui.main(["--help"])`
    in-process with `capsys`. The console-script node resolves `twitch-ia-compagnon-config` through
    `importlib.metadata.entry_points()` and calls it.
  - `test_profiles::test_installed_distribution_...` (allowlisted 19→21) replaces `pip install` into a
    temporary target with two in-process checks. The first reads the package data through
    `setuptools.config.pyprojecttoml.read_configuration` and resolves every shipped manifest in it.
    The second runs `--help` in-process. The 21-manifest count it asserts is unchanged from P11's
    adaptation.
  - `test_phase5_docs_status` (ac36, 2 nodes) ran child interpreters to count sockets. It now
    asserts its socket counter against the guard's refusals in-process.
  - `test_config_ui::test_ac5_the_socket_patch_is_effective` keeps its claim (its patch refuses
    sockets) and asserts it inside the guard.
  After this step `AC33_PENDING` is empty. The step deletes the ledger mechanism (the frozenset and
  the fixture's ledger branch) from `tests/conftest.py`. The only exemption left is P1's operator
  opt-in for a trial node, and it is inert in the default run.

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

[`tests/conftest.py`, `tests/test_stream_control.py`, `tests/test_proxy.py`, `tests/test_proxy_process.py`, `tests/test_installation_doc.py`, `tests/test_profiles.py`, `tests/test_phase5_docs_status.py`, `tests/test_config_ui.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P15d]

All dependencies are already merged into `main` when this step runs.

## Tests

Every converted node passes under the guard. `tests/test_ac33_isolation.py` drops the
  ledger checks and gains `test_the_guard_has_no_ledger`, which finds no `AC33_PENDING` symbol and
  no ledger branch. The full suite passes.

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

The in-process topology no longer proves that two OS processes cooperate, and the
  installed-distribution test no longer proves a real `pip install` works. Both are named in
  `summary.md` as fake-only (D8). The allowlisted `test_profiles` test now changes in mechanism as
  well as constant. That goes beyond the allowlist's "constants only", so it is flagged in the step
  report and checked in P17 (no assertion dropped).

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
