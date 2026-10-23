# Step P1 — Confine platform-named bytes in core and adapt the two core scans (DD1, DD2; prerequisite of R2, R3, R5, A1, A2; flagged out-of-list)

Plan step `P1` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Create `core/config_ui/locations.py` holding the product/distribution name constant
  and the per-platform directory functions A1 and A2 need: `default_profile_path(platform, environ)`
  (`$XDG_CONFIG_HOME/<product>/profile.yaml` falling back to `~/.config/<product>/`; `%APPDATA%\<product>\`
  on Windows; `~/Library/Application Support/<product>/` on macOS), `credential_directory(platform,
  environ)` (A1: the same per-user directory on Linux/macOS, `%LOCALAPPDATA%\<product>\` on Windows),
  `credential_file_name(profile_path)` (`<profile-file-name>.credentials.json`) and
  `product_version()` (installed distribution's version, `importlib.metadata`, with a value-free
  fallback when not installed). The platform and environment are parameters so every test forces them.
  Adapt `test_ac31_the_core_and_the_neutral_modules_name_no_platform` (the `core` parameter) and
  `test_the_broad_core_scan_names_no_platform_or_vendor` to skip exactly
  `{core/config_ui/locations.py, core/config_ui/profiles/, core/config_ui/strings/}` — a module-level
  frozenset, asserted to be exactly those three paths, all under `core/config_ui/` — and to assert that
  every other file under `core/` is still read; `test_ac39_core_names_no_platform` is not touched.
  Flag the adaptation in the commit message as an out-of-list edit for operator ratification.

## Requirements

Plan mapping: R2, R3, R5
Acceptance criteria owned by this step: (see plan)
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

[`core/config_ui/locations.py`, `tests/test_agentic_loop.py`, `tests/test_youtube.py`, `tests/test_first_run.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[] (none — can run first)

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_first_run.py`: AC1's path half — `default_profile_path("linux",
  {"XDG_CONFIG_HOME": tmp})` = `<tmp>/twitch-ia-compagnon/profile.yaml`, with `XDG_CONFIG_HOME` unset and
  `HOME=<tmp>` = `<tmp>/.config/twitch-ia-compagnon/profile.yaml`, Windows/macOS forms; credential
  directory per platform (Windows `LOCALAPPDATA`), credential file name for `a.yaml` vs `a.yml` distinct
  (AC11 naming). Each adapted scan is proved by calling its scan helper on a tmp tree, with fixtures
  matched to each scan's own contract: a fixture core file outside the exemption containing the
  lowercase word `twitch` fails **both** scans (it satisfies the first scan's case-sensitive substring
  and the second's case-insensitive word match); a second fixture containing only `Twitch` fails the
  broad case-insensitive scan (its case-insensitivity is kept) and is **not** claimed to fail the
  case-sensitive first scan; the same two fixtures placed inside an exempt path fail neither adapted
  scan. Both scans pass on the repository.

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

The exemption is a weakening of a guard if it grows: pin the set exactly and keep it
  limited to files this phase creates. `importlib.metadata` may not find the distribution in a bare
  checkout — `/instance` and the version comparison (P8) must use the same function so both sides agree;
  the fallback string must be stable. Writing the product name anywhere else in core (docstrings
  included) fails the scans: reviewers grep for it.

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
