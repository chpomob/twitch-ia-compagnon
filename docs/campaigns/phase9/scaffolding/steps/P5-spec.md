# Step P5 — Credential store and wizard state file (A1, A7, R4; AC11 storage half, AC30 state-file clause)

Plan step `P5` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`CredentialStore(profile_path, platform, environ)`: resolves the A1 directory via
  `locations.py`; on POSIX creates it 0700 if absent and refuses (no write, a value-free refusal reason
  for screen 4) when it is not owned by the user or is group/other-writable; on Windows refuses when
  `LOCALAPPDATA` is unset or not an existing directory; writes `<profile-file-name>.credentials.json`
  atomically (temp created 0600 via `os.open(..., 0o600)`, then `os.replace`), holding only `{NAME:
  value}` for names the profile lists under `secrets`; reads/replaces it only when the profile's state
  file records its digest, otherwise neither reads nor writes and returns a refusal naming the path.
  `WizardState(profile_path)`: `<base>.wizard.json`, 0600 on POSIX, atomic replace, holding profile
  choice + non-secret parameters, language, per-screen pass records for the current process only
  (successes are never persisted as passes beyond screen 1, A7), the digest of each file screen 4 wrote,
  and `completed`. Ownership check: `owns(path)` is true only while the file's bytes hash to the recorded
  digest. Never stores a credential value (an assertion in `save()` compares against the store's values).

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC11, AC30
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

[`core/config_ui/wizard.py`, `tests/test_first_run.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1, P4] (P4 first: both steps edit `core/config_ui/wizard.py`; P5 adds its classes
  after P4's generator so the file has one defined baseline)

All dependencies are already merged into `main` when this step runs.

## Tests

AC11 storage half — file exists under the per-user dir with mode `0o600`, holds the value
  under its reference name and no profile content; `a.yaml`/`a.yml` → 2 distinct files, the other left
  untouched; platform forced to Windows with `LOCALAPPDATA=<tmp>` → under `<tmp>/twitch-ia-compagnon/`;
  group-writable directory (POSIX) or `LOCALAPPDATA` unset (Windows) → no credential file and no file in
  the profile or per-user directory holds the value; a pre-existing credential file whose digest the
  state does not record is byte-identical afterwards and never opened for reading (spy on `open`).
  State file never contains a stored or submitted credential value (AC30 clause).

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

Creating the file then `chmod`-ing leaves a window: create with mode at `os.open`. The
  umask can widen nothing below 0600 but can narrow the directory; check ownership with `os.stat`
  (`st_uid == os.getuid()`) only on POSIX. Windows behaviour is simulated by forcing the platform value;
  the code must not call Windows-only APIs that are missing on Linux during tests.

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
