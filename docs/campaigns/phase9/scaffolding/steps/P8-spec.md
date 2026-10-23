# Step P8 — The instance record, the `/instance` route and the second launch (R1; AC31, AC4 `/instance` row)

Plan step `P8` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`ConfigUI` mints `instance_marker = secrets.token_hex(16)` (128 bits, distinct from
  the token). Add route `/instance` (GET only): after the Host and method guards, it checks the access
  token from the query exactly as the access URL does (constant time, same refusal status), creates no
  session and answers JSON `{"marker", "version"}` with `locations.product_version()`. In `main()` add an
  injectable `instance` seam bundle (`liveness`, `probe(port, url, timeout)`, `clock`, `sleep`,
  `platform`, `publish` hooks for the race fake): before serving, read `<base>.instance.json`.
  Classification exactly as R1 states — incomplete (unparseable, missing or wrong-typed field for the
  platform) or pid not live → stale; POSIX live pid → probe with the recorded token, each attempt ≤ 2 s,
  retried every 0.5 s, none started after 10 s since the first; recorded marker → confirmed (same version:
  opener with the recorded URL, "already running" line, exit 0; other version: report naming both, exit
  non-zero, nothing opened); other marker → stale; no confirming answer → report (record path, port fact:
  answering-but-unauthenticated vs not answering, cause, next action, 0 URLs), record kept byte-identical,
  exit non-zero; non-POSIX live pid → "running but not answering" report, no probe, kept, non-zero.
  Stale removal: rename to a launch-unique name, delete only if the bytes equal the judged ones, else
  restore with a no-replace rename and re-read. Publication: temp file (0600 on POSIX, from `os.open`),
  then `os.link` to the record name (fails if present) and unlink the temp; on Windows `os.rename`
  (fails if present); a lost race re-reads and behaves as a second launch. The record holds pid, port,
  marker and, only on POSIX, the access URL. A clean exit (a `finally` around `serve`) removes it only
  while it still holds this instance's marker. All report sentences come from the string table (P3).
  Extend the `test_ac35` route-set assertion with `/instance` (allowlisted entry; final set in P15).

## Requirements

Plan mapping: R1
Acceptance criteria owned by this step: AC31, AC4
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

[`core/config_ui/__init__.py`, `tests/test_config_ui.py`, `tests/test_first_run.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3, P6, P7] (P7: both edit `core/config_ui/__init__.py`; P8 builds on P7's
  `ConfigUI.__init__`)

All dependencies are already merged into `main` when this step runs.

## Tests

AC31 in full with fakes (opener, serve, liveness, probe, clock): confirmed same version
  (opener 1× with recorded URL, line printed 1×, exit 0, `serve` 0); first attempt > 2 s and a later one
  within 10 s confirms; record port 8766 vs `--port 8765` → 8766 URL; raising opener → `serve` 0, line
  printed; all attempts refused / > 2 s / wrong-token until 10 s → > 1 attempt, each ≤ 2 s, none after
  10 s, `serve`/opener 0, record byte-identical, report naming path and the port fact, 0 URLs, non-zero;
  stale (a)–(d) → "did not stop cleanly", record replaced by a fresh one with this marker, opener 0 with
  the old URL, `serve` exactly 1, no exception; non-POSIX publication without token and second-launch
  report; version mismatch; failure between temp write and publication → no file under the record name;
  the publication race → 1 `serve`, loser opens the winner's URL; mode `0o600`; token 0 times in logs,
  diagnostics, response bodies (probe's included), the child's env and the status record; clean exit
  removes the record. AC4 `/instance` row: wrong Host, wrong method, missing/wrong token (status as the
  access URL's wrong token, 0 sessions, 0 marker bytes); a good GET answers 200 with exactly marker and
  version, 0 sessions.

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

`os.link` is unsupported on some filesystems (FAT, some network mounts): treat `OSError`
  other than `FileExistsError` as a publication failure with a value-free diagnostic and refuse to serve
  rather than fall back to a replacing rename. The real probe uses an HTTP client — it must only ever be
  constructed in production; tests inject it (AC33). Liveness on POSIX reuses `process_alive`. A
  record removed by the clean-exit path while another launch is reading it must not crash that launch
  (treat as "no record").

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
