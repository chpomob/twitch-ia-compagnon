# Step P2 — Feed path, bounded redacted feed writer and its collision refusal (R6; AC18, AC19)

Plan step `P2` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

In `core/overlay.py` add `FEED_FILE_VARIABLE` (named in the existing
  `STATUS_FILE_VARIABLE` style), `default_feed_path(base)` (`<base>.feed.jsonl`, DD9) and a
  `feed_path_collision(feed, base, overlay, status)` on file identity (`same_file`). In `core/main.py`'s
  `run()`, when the variable names a path: refuse at startup, before the configuration is read, with a
  diagnostic naming which file it collides with (base, overlay or status), exactly as the status path
  does; otherwise create a `_FeedPublisher` that subscribes (last order) to the supervision facts —
  platform event arrived (kind, platform, author display name, excerpt ≤ 80 chars),
  `input.trigger.accepted/rejected`, `brain.admission.accepted/rejected`, run started, `brain.run.completed`
  (status + reason), refused actions, `module.ready/degraded/stopped` (with cause), runtime started and
  stopping. Each entry is redacted against the runtime's `secrets` (the loader's known credential set)
  before serialisation, truncated so the serialised entry is ≤ 1 024 bytes, appended to a 200-entry
  ring with a sequence number; a background writer rewrites the file atomically (temp + replace,
  owner-only on POSIX). The handler never raises and never awaits I/O; a write failure is reported once,
  value-free. Without the variable, nothing is subscribed and `run()` is byte-for-byte the same path.

## Requirements

Plan mapping: R6
Acceptance criteria owned by this step: AC18, AC19
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

[`core/overlay.py`, `core/main.py`, `tests/test_console.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_console.py` (against a fake-edged runtime/bus, no socket): AC18 — 250
  facts → exactly the latest 200 in publication order; a 5 000-char chat message → entry ≤ 1 024 bytes,
  excerpt ≤ 80 chars; a `secrets` value in the text is redacted; a raising writer leaves the publication
  completing and every handler invoked (and the failure reported once). AC19 — without the variable, 0
  feed files and the same bus history/status records (the existing runtime suites unmodified); a feed
  path resolving to base, overlay or status refuses at startup naming which.

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

The handler must not reorder or slow the bus (await nothing; a sync handler); the
  coordinator's drain tie on `ManualClock` (memory) can make shutdown assertions flaky — assert the
  outcome, not status 0. Redaction must run on the entry before truncation so a secret split by
  truncation cannot leak a prefix: redact, then truncate on character boundaries, then re-check size.
  Event names are taken from the existing constants, not retyped.

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
