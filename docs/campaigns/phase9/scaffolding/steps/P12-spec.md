# Step P12 — The five wizard screens, server-side gating, regeneration and re-entry, `/wizard` and `/language` (R5, A7, R2, R4; AC6 choice half, AC7, AC11 response sweep, AC13, AC16, AC17, AC28, AC29 screen half, AC30)

Plan step `P12` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

In `wizard.py`, a `Wizard` controller over `WizardState`, `CredentialStore`, the
  validators (P10), the checker and the dry-run seam (P11), all injected by `ConfigUI`. Screens: (1)
  language + profile + parameters (4 options in order, names/descriptions from the table); (2) platform
  credential (P10), identities from the answer, reward pick for channel points; (3) model (P10) or "not
  needed"; (4) write the generated profile (refuse if a profile appeared at the target after first-run
  detection, or a file exists that the state does not own byte-identically) and the credential file where
  A1 allows, else state the refusal and keep *next* closed; run `check_config` with the merged
  environment, naming an offending field path in plain language on exit 2; read-only YAML view
  (non-editable markup, 0 form controls posting profile content, redacted); (5) dry run → success only on
  a record with run id, outcome and `dry_run` origin whose outcome is the profile's expected one; then
  mark `completed` and link to the console. Gating: a request for screen n+1 is refused (re-rendering
  screen n) unless screen n's success is recorded for the current inputs' fingerprint; changing an input
  of a validated screen invalidates it and every later one. **Fingerprints are value-sensitive without
  persisting secrets:** `ConfigUI` mints a per-process key `secrets.token_bytes(32)` held only in memory;
  a screen's fingerprint is SHA-256 over its non-secret inputs plus, for each credential input,
  `hmac.new(key, value, sha256)` of the effective value (a submitted value, or the stored value when the
  input is left empty as "kept"). Replacing one non-empty token or API key with a different one changes
  the HMAC, so the screen and every later one are invalidated exactly like any other input change; a
  resubmission of the same value does not invalidate. The fingerprint table lives in memory only: the
  state file persists only screen 1's pass (whose inputs are non-secret, A7) and never a credential
  value, HMAC or fingerprint derived from one; a new process has a new key, which matches A7 (screens
  2–5 must pass again anyway). Re-entry per A7: only screen 1 resumes as
  passed; screens 2–5 must pass again; credentials show "kept" and are never re-rendered. Completed →
  every wizard route redirects to the console. Credential inputs render empty with a "kept" state;
  no response, redirect or link carries a stored or submitted value; failures render a cause + next
  action only. In `__init__.py` register `/wizard` (GET, POST) and `/language` (POST, persists the
  choice in the state file, DD3) behind the existing guard chain; the wizard never calls the supervisor.
  Extend the `test_ac35` route set with `/wizard`, `/language` (allowlisted).

## Requirements

Plan mapping: R5, R2, R4
Acceptance criteria owned by this step: AC6, AC7, AC11, AC13, AC16, AC17, AC28, AC29, AC30
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

[`core/config_ui/wizard.py`, `core/config_ui/__init__.py`, `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json`, `tests/test_config_ui.py`, `tests/test_first_run.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P5, P6, P7, P8, P9, P10, P11] (P8/P9: earlier editors of
  `core/config_ui/__init__.py`, and P8 already extended the `test_ac35` route set with `/instance`, the
  baseline P12 extends)

All dependencies are already merged into `main` when this step runs.

## Tests

AC2 wizard half (first run: following the P6 redirect with the session cookie renders
  screen 1 of the wizard, status 200, 4 profile options). Credential replacement (R5): pass screens 2–3
  with token `tok_A`, then submit screen 2 with `tok_B` → screens 2–5 recorded as not passed, advancing to
  3 is refused until screen 2 passes again, the platform validator is called again exactly once with
  `tok_B`; resubmitting `tok_B` unchanged after it passed keeps 2–5's records; the same for the model API
  key on screen 3 (invalidates 3–5, not 2); the state file's bytes contain neither `tok_A`, `tok_B` nor
  the hex or base64 form of any HMAC/fingerprint computed with them (the test recomputes them through
  the UI's key). AC6 choice half (`fr` on screen 1 renders the next page in French; survives a new
  `ConfigUI`). AC7 (exactly 4 options in order, both languages). AC13 (for each n in 1..4, advancing
  before n's success is refused, re-renders n, 0 calls on the next validator; changing a validated input
  invalidates n..5). AC16 (profile appeared after detection → refused, byte-identical; spoken keyword with
  a malformed detector → exit 2, field path named, *next* closed; read-only view has 0 posting controls;
  0 new routes accept YAML content; editor routes unchanged). AC17 (every AC14–AC16 failure and a dry run
  > 60 s on the fake clock: 0 `Traceback`, `File "`, exception class names, submitted values; 0
  supervisor `start` over a full successful pass). AC11 response sweep (profile, overlay and every
  response body including the YAML view and screen 2's wrong-client-id re-render: 0 occurrences of the
  value, URL-encoded, HTML-escaped, JSON-escaped; empty "kept" input; screen 4 names the Windows
  directory and why; refusal keeps *next* closed). AC28 (companion-name change after screen 4
  invalidates 1–5, re-passing replaces the profile with the new name, 1 profile + 1 credential file; an
  externally appended byte → screen 4 refuses, file byte-identical, cause names the editor; completed →
  new `ConfigUI` lands on the console). AC29 screen half (each malformed/absent record case and the 60 s
  timeout leave screen 5 open, state not completed, a cause shown, retry possible; silent succeeds on its
  record). AC30 (a)–(e) with fake endpoints, counting exactly 1 platform validation, 1 model request and
  1 dry-run call in the new instance. AC5 key check re-run.

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

This is the largest step; keep screens as small render/validate pairs and the gating as one
  fingerprint table so AC13 holds by construction. Fingerprints use a keyed in-memory HMAC of each
  credential value (never presence alone, which would not see a replaced token, and never a bare hash,
  which would let a short secret be guessed from a leaked digest); the key and the table must never be
  written to the state file, a log record or a response — the replacement test sweeps the state file
  for them. The guard order (Host, session, method, Origin, CSRF) is
  inherited from `ConfigUI.handle`; do not add a wizard-specific bypass for the first GET (the access URL
  creates the session, then redirects). Screen 4's "appeared after detection" test needs the first-run
  decision recorded at UI construction.

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
