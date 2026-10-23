# Step P10 — Platform and model validators behind injectable transports, with the plain-language failure map (R5 screens 2–3, D3; AC14, AC15, AC17 validator half)

Plan step `P10` of the approved Phase 9 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase9/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase9/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase9/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

In `wizard.py`, `validate_platform(profile, token, client_id, *, transport, clock,
  timeout=10)`: one GET to the profile's validation endpoint; outcomes map to distinct failure tokens —
  `connection_refused`, `timeout` (fake clock), `unauthorized` (401), `client_id_mismatch`,
  `missing_scope` (names the missing scope names, which are not secrets), `malformed_answer` — or a
  success carrying login and user id (broadcaster and bot identity filled from the user id). For channel
  points, `list_rewards(profile, token, client_id, user_id, …)` (one bounded read) → titles + ids, or
  `no_rewards` ("create the reward first"), or the same transport failures. `validate_model(endpoint,
  model, api_key, *, transport, clock, timeout=30)`: exactly one chat-completions request; outcomes
  `connection_refused`, `timeout`, `unauthorized` (401/403), `not_found` (404), `not_a_completion`
  (shape), `empty_answer`, or success; the silent profile short-circuits to "not needed" with 0 requests.
  Transports are protocols (production: a thin aiohttp adapter run via the existing socket-free loop
  helper pattern with a real connector only outside tests); every failure resolves to a string-table
  cause + next action pair, never an exception text or submitted value. Add those keys to both string
  files.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC14, AC15, AC17
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

[`core/config_ui/wizard.py`, `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json`, `tests/test_first_run.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3, P4, P5, P9] (P5: both edit `core/config_ui/wizard.py`; P9: the previous
  writer of `tests/test_first_run.py` in the shared-file chain)

All dependencies are already merged into `main` when this step runs.

## Tests

AC14 (each of the 6 outcomes a distinct cause with a next action and *next* closed; 200 with
  login `streamer_x` and user id `123` → `streamer_x` shown, identities `123` in the generated profile;
  2 rewards → 2 titled choices; empty list → its own cause). AC15 (6 outcomes distinct; success after
  exactly 1 request; silent → not needed, 0 requests). AC17 validator half: each failure's rendered text
  holds 0 `Traceback`, `File "`, exception class names or submitted values (a sweep over the cause
  sentences rendered with every token). AC5 key check re-run.

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

The timeout must be enforced by the validator's own bound, not the transport's, so a fake
  clock drives it deterministically. A malformed but 200 answer whose fields have wrong types must map to
  `malformed_answer`, not raise. No validator may log the request URL with query credentials.

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
