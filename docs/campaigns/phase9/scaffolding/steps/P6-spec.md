# Step P6 — `--config` optional, extended startup collisions, first-run landing and the browser opener (R1; AC1, AC2, AC3; allowlisted `test_a_missing_config_or_bad_port_exits_non_zero`)

Plan step `P6` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Before changing any render, capture the pre-phase editor base-page markup for a fixed
  fixture (tokens, nonces and field keys pinned by monkeypatching `secrets`) into
  `tests/fixtures/phase9/editor_base_page.html`. Then: `_parser()` makes `--config` optional;
  `UISettings.from_argv` resolves an absent `--config` to `locations.default_profile_path(sys.platform,
  os.environ)` and `UISettings` gains `wizard_path`, `credential_path`, `instance_path`, `feed_path`
  properties (canonical forms). `startup_checks` adds, after the existing checks and in their style,
  collision refusals for the wizard state, credential and instance files and the feed path against base,
  overlay, status and each other (one problem per collision, naming role, path and the collided file,
  never content). `main()` gains keyword-only `opener` (default: `webbrowser.open`) and `serve`
  (default: module-level `serve`) parameters; it creates the default profile's parent directory 0700 if
  absent (only when `--config` is absent), prints the access URL once, then calls the opener once in a
  `try` whose failure is logged value-free (the URL is never in a log record) and never stops the UI.
  The `/` handler routes the access URL's landing: first run (base absent) or an unfinished state →
  a redirect to `/wizard`; otherwise a redirect to `/console` (default location) or the unchanged editor
  (`--config`); DD7's link. P6 only owns the **routing decision** (`landing_target(settings, state)` plus
  the redirect issued after the access URL creates the session): the `/wizard` and `/console` handlers
  do not exist yet at this point, so P6 asserts the redirect's `Location` and the session cookie, never
  the target page's content. The end-to-end landing (the rendered wizard screen 1 / the rendered
  console) is asserted by P12 and P13 once those handlers exist.
  Adapt `test_a_missing_config_or_bad_port_exits_non_zero` per the allowlist: keep both bad-port
  assertions and assert a no-argument start with a colliding default (e.g. `--status-file` pointing at
  the default `<base>.wizard.json`) exits non-zero with `serve` never called.

## Requirements

Plan mapping: R1
Acceptance criteria owned by this step: AC1, AC2, AC3
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

[`core/config_ui/__init__.py`, `tests/test_config_ui.py`, `tests/test_first_run.py`, `tests/fixtures/phase9/editor_base_page.html`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1, P5]

All dependencies are already merged into `main` when this step runs.

## Tests

AC1 in full (default path, 0 problems, `--host 0.0.0.0` refusal, the six collision cases:
  status and overlay each set to `<base>.wizard.json`, `<base>.instance.json` and the credential file,
  each with fixture content, exactly 1 problem naming role/path/collided file with 0 occurrences of the
  content, `main([], serve=fake)` non-zero and `serve` 0 calls). AC2 routing half (first run: the access
  URL answers with a session cookie and a redirect whose `Location` is `/wizard`; an unfinished state
  file: `Location` `/wizard`; a profile at the default location with no `--config`: `Location`
  `/console`; `--config`: the editor itself, whose markup equals the captured fixture modulo the one
  added link — fully verifiable here because the editor exists). The wizard/console halves of AC2 are
  completed in P12 (wizard page rendered after following the redirect) and P13 (console page rendered
  after following the redirect). AC3 (opener exactly once with `ui.access_url`,
  URL printed exactly once, 0 log records with the token; a raising opener → exit 0, URL printed). The
  existing `main([...])` tests of `tests/test_config_ui.py` still pass unmodified (their default opener
  is refused by the AC33 guard and swallowed by design — asserted once in `tests/test_first_run.py`).

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

Existing tests calling `main` now reach the real `webbrowser.open`; under the AC33 guard
  its process start raises and is swallowed. If a `webbrowser` path on the test machine opens a browser
  without going through a guarded entry point, it could open a real browser: the step checks the Linux
  `webbrowser` code paths, injects the opener in every new test, and records the residual risk in the
  commit message. Production code gets no test-awareness. Creating the default directory must not happen for `--config` runs (behaviour
  unchanged, A2). The base-page fixture must be captured from the unmodified code in the same step, first.

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
