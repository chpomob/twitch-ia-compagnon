# Step P1 — Phase-7 baseline of the shipped profiles, and the whole-suite isolation guard (R4 AC16 baseline half; R9 AC33)

Plan step `P1` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- Before any production change (HEAD `7782e5f`), drive each shipped `*.example` profile
    (`config.yaml.example`, `config.server.yaml.example`, `agent.yaml.example`,
    `presence.yaml.example`) against the existing fakes in `tests/conftest.py` on one fixed event
    sequence (chat messages, each phase-7 notice kind, a `cue`, a `watch_tick`) and record per profile:
    every trigger decision as `(input, reason, policy_version)`, every EventSub/platform subscription
    the fakes saw, and every admission result. Write the record as sorted JSON to
    `tests/fixtures/phase8/profile_baseline.json`.
  - In `tests/test_declared_triggers.py`, add the comparison test
    `test_ac16_shipped_profiles_decide_as_in_phase7` that replays the same sequence and asserts 0
    differences, and `test_ac16_runtime_dependencies_are_exactly_aiohttp_and_pyyaml` (parses
    `pyproject.toml`).
  - In `tests/conftest.py` add a `RecordedRandom` helper. It serves a given list of values, counts
    `ordinal` and can be re-seeded. It is only added if `tests/test_triggers.py` does not already
    expose one that can be imported.
  - In `tests/conftest.py` add the **autouse** fixture `_ac33_isolation` (D8). It applies to every
    test of the suite and uses `monkeypatch`, so it is undone after each test. While it is active:
    - **Socket creation raises** `AssertionError(f"AC33: {nodeid} created a socket")`. This covers
      `socket.socket.__init__` for any family when no `fileno` is given, `socket.create_connection`,
      `socket.create_server`, `socket.fromfd`, `socket.getaddrinfo` and `socket.socketpair`. The
      only exception is a `socketpair` whose caller frame is
      `asyncio.selector_events.BaseSelectorEventLoop._make_self_pipe`, the event loop's own wakeup
      pair. Wrapping the fds of that permitted pair (`socket.socket(..., fileno=...)`) is allowed.
    - **Process spawning raises** in the same way. This covers `subprocess.Popen.__init__` (which
      `subprocess.run`/`check_output` and asyncio's `create_subprocess_exec`/`_shell` all reach),
      `os.system`, `os.popen`, `os.posix_spawn`, `os.posix_spawnp`, `os.fork`, `os.forkpty` and the
      `os.exec*` family.
    - **Transitional ledger.** A module-level `AC33_PENDING: frozenset[str]` holds the node ids
      (`file::test`, parameter suffix stripped) of the 166 pre-existing tests the D8 probe found. The
      probe's list is committed verbatim in this step. For a node in the ledger the fixture yields
      without patching. The ledger may only shrink: P15a–P15e each remove the entries they convert,
      and P16 asserts it is empty. No other test may be added to it.
    - **Operator trials stay possible.** The real-instance trial files `test_phase2_trials.py`,
      `test_phase3_trials.py` and `test_phase5_trials.py` skip every node unless an operator sets
      that node's opt-in variable. The default run sets none, which is why 20 nodes are skipped. The
      guard yields unpatched only for a trial node that *is* opted in, decided by each file's own gate
      (for example `opted_in(question)` at `tests/test_phase5_trials.py:177`). In the default run no
      trial is opted in, so every executed test runs under the guard. This is a permanent rule, not a
      ledger entry, and P16 proves it is inert in the default run.
  - Every test that P2–P15 add runs under the guard from the start, because none of them is in the
    ledger. The extended `test_twitch.py` and `test_audio_input.py` tests are covered as well. No
    per-file mark is needed.

## Requirements

Plan mapping: R4, R9
Acceptance criteria owned by this step: AC16, AC33
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

[`tests/fixtures/phase8/profile_baseline.json`, `tests/test_declared_triggers.py`, `tests/conftest.py`, `tests/test_ac33_isolation.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

The two AC16 tests pass at `7782e5f` and at every later step. The new
  `tests/test_ac33_isolation.py` holds the guard's own tests:
  - `socket.socket()`, `socket.socket(AF_UNIX)`, `socket.create_connection(("127.0.0.1", 1))` and a
    direct `socket.socketpair()` each raise.
  - `subprocess.run(["true"])`, `asyncio.run(asyncio.create_subprocess_exec("true"))` and
    `os.system("true")` each raise. Each call is made inside `pytest.raises`, so nothing starts.
  - `asyncio.run(asyncio.sleep(0))` succeeds, which proves the self-pipe exception works.
  - Every ledger entry names a collected test (no stale entries), read from
    `request.session.items`.
  - The ledger is a subset of the committed probe list, so it has not grown.
  - With no opt-in variable set, no trial node is exempt.
  The full suite passes with the guard on, apart from ledger nodes, which run as before.

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

The ledger hides the 166 nodes until P15a–P15e convert them, so it is a real
  temporary exemption. That is why the ledger may only shrink, why its content is the probe's
  verbatim, and why P16 fails on any leftover. Matching the self-pipe by caller frame is
  fragile across Python versions. The self-test `asyncio.run(asyncio.sleep(0))` catches a mismatch
  at once, and the match uses the function's qualified name, not a line number. A guard that
  patches `subprocess.Popen.__init__` must not break pytest itself: pytest starts no process during a
  test, and the fixture is function-scoped, so collection and reporting are unaffected.
  A baseline taken after a production edit would hide a regression: the step commits the
  fixture before touching `core/`. Non-deterministic fields (timestamps, random message ids) must be
  excluded from the record or the comparison is flaky; the record keeps reasons, versions and ids
  derived from the fixed sequence only.

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
