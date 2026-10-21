# Step P15a — AC33 conversion

Plan step `P15a` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

These ledger nodes start `git`, `grep` or `python` only to read facts about the
  repository. Each is converted to an in-process reader in `tests/conftest.py`:
  - `files_containing(root, needle, ignore_case)` is a pure-Python `rglob` that skips
    `__pycache__`. It replaces `grep -r`/`grep -c` in the `*_names_no_platform` tests of
    `test_agentic_loop`, `test_capture`, `test_chat_context_module`, `test_users` and `test_proxy`.
  - `tracked_files(pattern)` parses the git index (`.git/index` format v2–v4). It follows a worktree's
    `.git` file to its `gitdir`. It replaces `git ls-files` in `test_phase5_docs_status` (two ac37
    nodes) and in `test_automation_bridge`.
  - `ignored(path)` matches `.gitignore` patterns with `fnmatch`, with directory and negation rules.
    It replaces `git check-ignore` in `test_hygiene::test_n8_...`.
  - For "zero changed lines since the phase base" (`test_input_ownership::test_ac2_...` against
    `f900f27`, `test_web::test_ac24_...` against `7c29434`): the step writes the four shipped
    `*.example` files *as they were at that commit* to
    `tests/fixtures/baselines/<commit>/`. They are taken with `git show` at authoring time, outside
    the suite, and the commit hash is recorded in a `SOURCE` file. The test then asserts byte
    equality, which is the same claim as `git diff --numstat` reporting 0 lines.
  - Tests that ran `pytest` in a child process are read through `ast` instead. These are
    `test_input_ownership::test_ac44_...`, which counts ownership rejections in the integration
    scenarios, and the `--collect-only` node in `test_automation_bridge`. The ac44 node imports the
    scenario coroutines of `test_integration.py` and runs them in-process with a counting subscriber.
    The collect node parses the test file's functions with `ast`.
  - `test_viewer_memory::test_ac19_cli_...` ran `python -X importtime`. It now walks the
    module-level imports of `modules/viewer_memory/__main__.py` and its in-repo transitive imports
    with `ast`, and asserts that no network client is reachable, as before.
  Each converted node is removed from `AC33_PENDING`.

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

[`tests/conftest.py`, `tests/test_agentic_loop.py`, `tests/test_capture.py`, `tests/test_chat_context_module.py`, `tests/test_users.py`, `tests/test_proxy.py`, `tests/test_hygiene.py`, `tests/test_input_ownership.py`, `tests/test_web.py`, `tests/test_phase5_docs_status.py`, `tests/test_automation_bridge.py`, `tests/test_viewer_memory.py`, `tests/fixtures/baselines/f900f27/`, `tests/fixtures/baselines/7c29434/`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P15]

All dependencies are already merged into `main` when this step runs.

## Tests

Each converted node keeps its assertions and passes under the guard. Two negative
  checks prove the readers are sensitive:
  - A temporary file containing `twitch` under `tmp_path` is found by `files_containing`.
  - A fixture that differs by one byte from the shipped profile fails the equality check.
  The ledger lost exactly the converted ids.

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

An index parser that misses extension records or the split index would list the wrong
  files. The parser is checked against a `git ls-files` list recorded at authoring time and committed
  as a fixture. A baseline fixture copied from the wrong commit makes the "zero changed lines"
  claim vacuous, so `SOURCE` records the hash and the step report quotes `git show <hash>:<file> |
  sha256sum` next to the fixture's digest.

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
