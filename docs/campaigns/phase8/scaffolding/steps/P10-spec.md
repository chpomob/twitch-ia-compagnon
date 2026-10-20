# Step P10 — The press command `python -m modules.operator press` (R6, A8; AC40, AC21 hotkey third)

Plan step `P10` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`main(argv=None, *, opener=None) -> int`: argparse `press <button> [--profile
  PATH]` (default `config.yaml`); loads the profile with `yaml.safe_load`, reads
  `modules.operator.settings.listen` and the `token` reference, resolves only a `${NAME}` token from
  `os.environ` (no other interpolation, no loader); sends one POST to
  `http://<host>:<port>/press` with JSON `{button, surface: "hotkey"}` and the token header via
  `urllib.request` (`opener` defaults to `urllib.request.build_opener().open`, timeout 5 s); exit 0
  when the input answered accepted, 2 when it answered a refusal, 3 on `URLError`/`OSError`/timeout,
  64 on a usage or profile error (missing button, unreadable/invalid YAML, missing listen/token).
  Messages to stdout/stderr are fixed strings and never contain the token. The module imports only
  the standard library and `yaml`; importing it does not import `core.runtime`, `core.loader`,
  `core.lifecycle` (and `modules/operator/__init__.py` imports nothing from them at module level).

## Requirements

Plan mapping: R6
Acceptance criteria owned by this step: AC40, AC21
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

[`modules/operator/__main__.py`, `tests/test_operator.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P9]

All dependencies are already merged into `main` when this step runs.

## Tests

AC40 (fake opener: exactly 1 POST to the configured address's `/press` naming `hype`,
  surface `hotkey`, the token; 0 on accepted; 2 for each core refusal — unknown button, cooldown,
  wrong token, `hotkey` disabled — with 0 events, the fake opener routing to P9's request core; 3 on
  connection error and on timeout; 64 with no button and with an unreadable profile; captured
  stdout/stderr never contain the token; the `sys.modules` purge-and-import check leaves the three
  core modules absent); AC21 hotkey third (a press via `main([...], opener=<core-backed fake>)`
  produces the third `manual` event with surface `hotkey`, completing AC21 with P9's two).

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

`python -m modules.operator` executes `modules/operator/__init__.py` first, so a
  module-level core import there would break A8 — guarded by the AC40 import test. A fake opener
  that does not exercise the real URL/headers would be pass-by-mock; the test asserts on the
  `Request` object the command built (URL, method, body, header names) and routes that to the real
  core.

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
