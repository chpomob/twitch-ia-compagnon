# Step P13 — The console page, the feed poll and plain-language reasons (R7, R2; AC20, AC21)

Plan step `P13` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`console.py`: `read_feed(path, after=cursor)` (bounded read of the P2 file, tolerant of
  a missing or partly written file), `render_console(state, feed, health, grants, language, table)` with
  the runtime state (running under this UI / not running / another unsupervised process, from the status
  record and the supervisor's child), each enabled module ready/degraded with cause and next action,
  feed entries newest first, a link to the editor, and `reason_sentence(token)` mapping every reason
  token to a table sentence (unknown → the generic sentence naming the token). `__init__.py` registers
  `/console` (GET) and `/feed` (GET, session-gated JSON poll with `?after=<sequence>`), both behind the
  existing guards; everything rendered passes through the R4 redaction. Add every reason sentence to
  both tables. Extend the `test_ac35` route set with `/console`, `/feed` (allowlisted).

## Requirements

Plan mapping: R7, R2
Acceptance criteria owned by this step: AC20, AC21
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

[`core/config_ui/console.py`, `core/config_ui/__init__.py`, `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json`, `tests/test_config_ui.py`, `tests/test_console.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2, P3, P6, P7, P12] (P12: both edit `core/config_ui/__init__.py`, the two
  string tables and the `test_ac35` route set in `tests/test_config_ui.py`; P13 extends P12's set;
  P6: the landing redirect P13's AC2 half follows)

All dependencies are already merged into `main` when this step runs.

## Tests

AC2 console half (a profile at the default location, no `--config`, finished or absent
  state: following the P6 redirect with the session cookie renders the console page, status 200, with
  the runtime state and the editor link). AC20 — collect every `REASON_*` constant exported by `core/triggers.py`, `core/admission.py`,
  `core/actions.py` and every reason token emitted on `input.trigger.rejected`,
  `brain.admission.rejected`, `brain.run.completed` and refused actions by the shipped modules (AST/regex
  over the sources, in-process — no `grep` process); each has a sentence in both languages; `zz_unknown`
  renders a non-empty sentence containing it. AC21 — fixture feed (arrived, run started, run completed,
  `self_authored` rejection, `audio_output` degraded with cause) renders exactly 5 items newest first
  (degraded first, arrived last), the rejection in French when `fr` is chosen, the degraded module with
  cause and next action; the poll with the newest cursor returns 0 entries.

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

Reason tokens emitted as literals inside modules (not constants) are easy to miss: the
  collector must scan string literals passed as `reason=` in the shipped modules and the test must fail
  on a new uncovered token. Module causes are module-owned text (memory: failures name the module only;
  the cause is on `module.degraded`) — render it as quoted data, redacted, with a table next action.

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
