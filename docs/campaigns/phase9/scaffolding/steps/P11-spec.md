# Step P11 — The dry-run entry and its run record (R5 screen 5, DD6; AC9, AC29 record half)

Plan step `P11` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add `DryRunRecord` (run_id, outcome, reason, origin, event) and `async def
  dry_run(config_path, event, *, environ, model_transport=None, clock=None, timeout=60.0)` to
  `core/main.py` as DD6 describes: same load/validation as `check_config`; the runtime assembled with
  `_assemble_runtime`; every enabled module activated with its `_session_factory` seam set to an inert,
  socket-free session (the brain's model transport is the injected one, or the real one in production);
  a recording provider bound in place of each action the platform input module provides; the fake event
  evaluated by the context's `TriggerEngine` under the profile's resolved policy and, when accepted,
  published with the input's event shape so the brain's admission scheduler records a run; the record's
  run id and outcome are read from the scheduler's record for that event (completed status), or, when
  the trigger rejects, `not_triggered` with the rejection's reason token and 0 model requests. Bounded
  by `timeout` on the injected clock; the runtime is always closed. The record's origin is `dry_run` and
  names the fake event. `core.main`'s command line is unchanged.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC9, AC29
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-9 decisions that override any contrary reading of a requirement:
- **One surface.** The wizard and the console live in the existing local application: same binding
  rules (loopback, token, CSRF, origin), same redaction rules, no second server and no second token.
  No page, log or status record may ever carry a secret value; the existing renderers already refuse.
- **Every step is gated by an observed success.** Twitch must authenticate and name the channel, the model
  must answer a real completion, the profile must pass `--check-config`, and the dry run must produce a
  recorded run (id, outcome, origin). A step whose observed success is absent cannot be completed.
- **Four profiles only**, each `--check-config` clean, each with a recorded dry run: chat Q&A, channel
  points, spoken keyword, silent. The silent one is honest: its dry run records a run without pretending
  a trigger fired.
- **The credential file lives in a per-user data directory on every platform** (0600 on POSIX, the
  `%LOCALAPPDATA%` ACL on Windows), is never logged, never rendered, never in a status record, and is
  named from the profile file name so two profiles never share one. Where a platform restricts nothing,
  the wizard refuses to write it and says so.
- **A slow UI is not a dead one.** The instance record is written atomically, counts only when the probe
  authenticates with the token, and a record whose probe fails is reported, not deleted. A second launch
  never starts a second server and never silently opens a different version.
- **Causes are translated; raw detail is data.** A failure shows a string-table sentence (English or
  French) and, behind an explicit disclosure, the redacted raw tail. The cause never quotes the detail.
- **The YAML is hidden, then read-only.** The wizard never asks for YAML and never edits a file it did not
  write in the current pass; the existing editor stays unchanged for operators.
- **No new runtime dependency, no new socket in a test.** Validators take injectable transports; the
  suite opens no network socket and spawns no process outside the spec's named exemptions.
- **The criterion is measurable.** From an installed package to a first run: under fifteen minutes, no
  terminal, no YAML (Amendment A1). The trial record states the platform, the install method and its
  duration, and the elapsed first-run time; the gate refuses an approval whose record omits any of them.
- Close exactly the requirement(s) this step names. If a sibling path shows the same defect, report it in
  your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. The full suite is green at every step:
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.

## Files

[`core/main.py`, `tests/test_first_run.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2, P4, P10] (P2: both edit `core/main.py`; P10: the previous writer of
  `tests/test_first_run.py`)

All dependencies are already merged into `main` when this step runs.

## Tests

AC9 — with a fake model answering `"bonjour"`: chat Q&A mention → 1 run started and 1
  completed (success), answer `bonjour`, 0 messages to the platform (the recording provider holds the
  proposal, nothing sent); no mention → 0 runs, 1 trigger rejection with its reason; channel points
  picked reward id → 1 run, other id → 0; spoken keyword wake word → 1 run; silent chat message → 0 runs,
  1 rejection with reason, 0 model requests. AC29 record half — for the 3 triggering profiles the run id
  equals the scheduler record's and the feed's (feed path supplied) run started/completed entries; silent
  → `not_triggered` + reason token; origin `dry_run` naming the event.

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

Replacing the platform module's network seam must not suppress its trigger declarations,
  which the registry needs to resolve the policy (activate the module's declaration path, not its
  transport). Modules that spawn processes (audio_input's detector/transcription commands) must not
  start one during a dry run — feed the spoken-keyword event after the wake-word decision through the
  hooked-type path with a fake transcript, and assert 0 process starts (AC33 guard). The core must stay
  platform-free (DD1): identify the platform module from manifest input ownership, never by name.
  Shutdown on `ManualClock` may report "timed out" (memory: assert the outcome).

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase9): ...`, `fix(phase9): ...`, `test(phase9): ...`, `refactor(phase9): ...`, `docs(phase9): ...`).
  The scope MUST be `phase9`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase9)`.
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
