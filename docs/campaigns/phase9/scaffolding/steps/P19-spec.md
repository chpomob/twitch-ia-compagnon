# Step P19 — Whole-suite run and full-branch review gate (all of R1–R8; AC1–AC32; DD1–DD9)

Plan step `P19` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Run the full suite under the AC33 guard and record the result. Then review the whole
  branch diff `b7ec50d..HEAD` together, not commit by commit: per-commit reviews miss cross-file bugs by
  construction — each step can be correct alone while the set is broken where files meet (e.g. the
  credential store's secret values reaching the redaction filter only through `ConfigUI` construction
  order, the feed path reaching the supervisor's environment, the route set seen by the guard chain, the
  wizard's state file read by the landing logic, the dry run's run id matching the feed's). The review
  checks: every new route behind the guard chain in order; no token or credential value in any file,
  log, environment, feed or response (grep the test artefacts); the DD1 exemption is exactly three paths
  and no other `core/` file names a platform or vendor; `*.example` unchanged; dependencies unchanged;
  every allowlisted test adapted without loosening; the out-of-list edits (DD1 scans; any P16 ignore-rule
  change; DD7 outcome) listed for operator ratification. Write the review and the phase summary (what
  was built, the flagged decisions, and that the fifteen-minute claim awaits the operator trial record
  of P18).

## Requirements

Plan mapping: R1, R8
Acceptance criteria owned by this step: AC1, AC32
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

[`docs/campaigns/phase9/full-branch-review.md`, `docs/campaigns/phase9/summary.md`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14, P15, P16, P17, P18]

All dependencies are already merged into `main` when this step runs.

## Tests

The full suite (`pytest`) green with 0 new AC33 exemptions and 0 new process-spawning
  nodes; the AC5 referenced-key check and the AC20 reason coverage re-run over the final sources.

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

A green suite does not prove D1's criterion (only the trial record does); the review must
  not approve while the record is unfilled if the gate requires it. Cross-step regressions surface here
  late — fixes go back to the owning step's files, each flagged in the review.

## Shared-file ordering

Every file edited by more than one step is edited along one dependency chain, so each step starts from
a defined baseline (each step lists its immediate predecessor in the chain among its Dependencies):

| File | Steps, in chain order (each depends on the previous) |
|---|---|
| core/config_ui/wizard.py | P4 → P5 → P10 → P12 |
| core/config_ui/__init__.py | P6 → P7 → P8 → P9 → P12 → P13 → P14 → P15 |
| core/config_ui/strings/en.json, fr.json | P3 → P10 → P12 → P13 → P14 → P15 |
| core/config_ui/console.py | P13 → P14 → P15 |
| core/main.py | P2 → P11 |
| tests/test_config_ui.py (incl. the cumulative `test_ac35` route set) | P6 → P8 (`/instance`) → P12 (`/wizard`, `/language`) → P13 (`/console`, `/feed`) → P14 (`/start`, `/stop`) → P15 (`/revoke`, final exact set) |
| tests/test_console.py | P2 → P13 → P14 → P15 |
| tests/test_first_run.py | P1 → P3 → P4 → P5 → P6 → P7 → P8 → P9 → P10 → P11 → P12 → P16 → P17 → P18 (each step appends its own test functions) |

## Caller enumeration

API changes: `UISettings.from_argv` / `_parser` (`--config` optional, `--install-launcher`), `UISettings`
(new derived paths), `startup_checks` (more problems), `core.config_ui.main` (keyword-only `opener`,
`serve`, instance seams), `ConfigUI.__init__` (merged environment, instance marker, wizard/console
collaborators), `ConfigUI.routes` (8 new entries), `Supervisor(...)` (environment carries stored
credentials and the feed path), `core.main.run` (feed variable), `core.main.dry_run` (new). Search:
`grep -rn "UISettings\.from_argv\|UISettings(\|Supervisor(\|config_ui\.main\|startup_checks(\|\.routes\b"`
over `core/ modules/ demo/ tests/` (4 sites in `core/config_ui/__init__.py`, 61 in
`tests/test_config_ui.py`, 1 each in `tests/test_input_ownership.py`, `tests/test_main.py`,
`tests/test_status_record.py`). Dynamic or reflective callers are not detected.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/config_ui/__init__.py | `main()` | P6/P8/P9: no `--config` → A2 default; opener once after checks; instance-record judgement before serving; `--install-launcher` exits; unchanged for `--config` apart from the opener call and the instance record |
| core/config_ui/__init__.py | `ConfigUI.__init__` → `Supervisor(default_launch_argv(settings), self.status_path)` | P7: pass `environ=` merged environment + feed path |
| core/config_ui/__init__.py | `startup_checks()` (called by `main`) | P6: extra collision problems only; existing messages byte-identical |
| core/config_ui/__main__.py | `main()` entry | No change (delegates) |
| pyproject.toml | console script `twitch-ia-compagnon-config-ui` | No change; valid with no argument |
| tests/test_config_ui.py | `UISettings.from_argv`, `Supervisor(...)`, `ConfigUI(...)`, `main([...])`, `ui.routes` | Existing calls keep passing `--config`/explicit `environ`; `test_a_missing_config_or_bad_port_exits_non_zero` (P6) and `test_ac35…` route set (P8, P12–P15) adapted per the allowlist; serving `main` calls hit the default opener, refused by the AC33 guard and swallowed (A3) |
| tests/test_input_ownership.py, tests/test_main.py, tests/test_status_record.py | `UISettings(...)` / `from_argv` / `core.main.main` | Keep passing `config=`; `core.main`'s CLI unchanged (`--config` stays required) |
| core/main.py | `run()` callers (`main`, tests across the runtime suites) | P2: behaviour identical without the feed variable (AC19) |
| demo/ | — | No caller of the changed APIs |

## Requirement coverage

| Requirement | Steps | Acceptance criteria |
|---|---|---|
| R1 | P1, P6 (landing routing), P8, P9, P12 (wizard landing), P13 (console landing), P15 (route set) | AC1, AC2, AC3, AC4, AC27, AC30, AC31 |
| R2 | P3, P10, P12, P13, P14 | AC5, AC6, AC32 |
| R3 | P4, P11 | AC7, AC8, AC9, AC10 |
| R4 | P5, P7, P12, P14 | AC8, AC11, AC12, AC32 |
| R5 | P4, P10, P11, P12 | AC9, AC13, AC14, AC15, AC16, AC17, AC28, AC29, AC30 |
| R6 | P2, P11 (feed run ids) | AC18, AC19, AC20 |
| R7 | P13, P14, P15 | AC20, AC21, AC22, AC23, AC32 |
| R8 | P16, P17, P18, P19 | AC24, AC25, AC26 |
| A1–A7, A3b | A1/A2 P1, P5, P6; A3 P6, P9; A3b P17, P18; A4 P4, P10, P11; A5 P10; A6 P15; A7 P5, P12 | — |

## Ordering rationale

Beyond functional needs, steps that edit the same file are chained explicitly (see "Shared-file
ordering"): P5 after P4 and P10 after P5 (`wizard.py`), P8 after P7, P9 after P8, P12 after P8/P9 and
P13 after P12 (`__init__.py`, the string tables and the cumulative `test_ac35` set), and the
`tests/test_first_run.py` chain. AC2 is split by what exists when: P6 asserts the landing *routing*
(redirect target and session cookie, and the editor page, which already exists); the wizard and console
pages behind those redirects are asserted end to end in P12 and P13, which create them.

P1 comes first because every later step that writes a platform-named byte under `core/` (strings,
profiles, the per-user directories) would otherwise turn three existing guards red; confining those
bytes and pinning the exemption before any of them exists keeps the suite green at every step. P2 (the
runtime feed) is independent of the UI and is needed by the supervisor environment (P7), the dry run's
feed correlation (P11) and the console (P13). The string table (P3) precedes every step that renders a
sentence; the profiles (P4) precede the validators (P10, which read endpoints and scopes from profile
data) and the dry run (P11, which drives the profile's fake event). The credential store and state file
(P5) precede the settings/landing work (P6, which routes on the state file and adds their collision
checks) and the environment merge (P7). The instance record (P8) needs the table for its reports and
P6's `main` seams; the launcher (P9) needs P6's argument parsing. The wizard screens (P12) assemble P5,
P6, P7, P10 and P11 and therefore come after all of them; the console (P13) needs the feed and the table,
Start/Stop (P14) builds on the console page, and Revoke with the final route-set assertion (P15) comes
last among the routes so the allowlisted exact-set test is finalised once every route exists (each
earlier route step extends it, so it never fails in between). Package data (P16) needs the data files of
P3/P4; the guide (P17) describes the finished screens and console; the trial template (P18) is
independent in content but is placed after P17 because both append to `tests/test_first_run.py`; the full-branch review (P19) is last because it inspects all changed
files together.

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
