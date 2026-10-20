# Step P9 — The `operator` input

Plan step `P9` of the approved Phase 8 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase8/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase8/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase8/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- Manifest v2 (modelled on `modules/watch/module.yaml`): `produces: [channel.chat.message]`,
    `lifecycle.roles: [input]`, `credentials: [token]`, `settings_validator: validate_settings`;
    settings `buttons` (list of `{id, label, prompt_text, cooldown_seconds (0–3600, default 2),
    rect?: {x, y, w, h} percent}`), `destination {platform, channel_id}`, `surfaces {page, hotkey,
    overlay}` (each default `false`), `listen {host (default 127.0.0.1), port, allow_non_loopback
    (default false)}`, `token` (`${...}` credential), `overlay {image?}`; `triggers.fields:
    {button.id: string, button.surface: string}`; types: declared `button` (kinds `[manual]`,
    required `button_ids` array, rule `{in: [{field: button.id}, {param: button_ids}]}`) and generic
    `event_kind` narrowed to `[manual]`; default policy `event_kind{kinds: [manual]}`.
  - `validate_settings`: no surface enabled, token empty or < 32 characters, a button without
    `prompt_text`, a non-loopback host without `allow_non_loopback` → diagnostics naming the field,
    no value echoed; run by the loader before any listener.
  - Request core `OperatorCore.handle(method, path, headers, body) -> (status, body)`, socket-free:
    constant-time token compare (`hmac.compare_digest`), accepted `Host` set (the listen address),
    on press an `Origin` other than the page's own (when sent) refused; unknown button, surface
    disabled, cooldown (per button, on the injected clock, last *accepted* press) refused; every
    refusal increments a named count (`refused_token`, `refused_host`, `refused_origin`,
    `refused_unknown_button`, `refused_cooldown`, `refused_surface`), none carries a header value. An
    accepted press builds one schema-2 `manual` event (`payload.button = {id, surface}`, text =
    `prompt_text`, author `operator:<id>` with the label, a fresh `message_id`
    `operator:<id>:<uuid4 hex>`, destination), decides it with `context.triggers.evaluate` and admits
    an accepted one through the scheduler resolved like watch's (`("admission", "runs")` service
    fallback), trace via `decision_trace_payload`. GET `/` lists buttons, GET `/overlay` renders the
    image with absolutely positioned buttons from `rect`, POST `/press` is the press endpoint (used by
    page, overlay and the A8 command); all three take the token as a query/header parameter and the
    pages post it back without logging.
  - Lifecycle: `prepare` validates and binds nothing; `start_inputs` opens the aiohttp listener
    through the `_server_factory` seam (default: `aiohttp.web.AppRunner` + `TCPSite`, access log
    disabled); `stop_inputs` closes it and the core refuses every later press. No module-level
    import of `core.runtime`, `core.loader`, `core.lifecycle` (AC40's last clause).
  - `pyproject.toml`: add `"modules.operator" = ["module.yaml"]` (spec target; phase-1 precedent).

## Requirements

Plan mapping: R6
Acceptance criteria owned by this step: AC21, AC22, AC23, AC24
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-8 decisions that override any contrary reading of a requirement:
- **A module declares its trigger types; the kernel evaluates them.** The four generic types
  (`probability`, `audience`, `keyword`, `event_kind`) stay and remain usable by any input. A declared
  type carries a declarative match rule; no module code runs in the hot path for it.
- **A declared type narrows, never widens, and grants nothing.** Configuration is validated against the
  module's declared schema *and* the kernel's canonical one. A type cannot assert a field its input
  does not emit, and a trigger only decides candidacy: admission, budgets and DEFAULT-DENY are
  untouched and still decide everything after it.
- **The one hook is bounded and fail-closed.** It runs only on events its own module emitted, under a
  hard timeout; raising or exceeding the bound means *no trigger*, never a silent pass, and its outcome
  is recorded like every other decision.
- **Event kinds are appended, never reordered.** `reward` and `manual` join the closed vocabulary at the
  end, the way `cue` did; earlier indices keep their order and meaning.
- **No new runtime dependency.** `aiohttp` and `PyYAML` remain the only ones. The wake-word detector is
  configured (a command or an HTTP endpoint), exactly like an audio source or the transcription
  endpoint; a detector is never embedded and never assumed.
- **Streamer.bot stays optional.** Its `cue` path is unchanged, and nothing in this phase needs it.
- **No continuous listening.** Audio publishes an event only when a trigger or a run asked for it; a
  run that nobody requested must not record audio, and continuous transcription is out of scope.
- **No test opens a socket.** Twitch, the detector, the surfaces and the audio device are faked; the
  real-instance trial stays a documented manual procedure with its own record.
- Close exactly the requirement(s) this step names. If a sibling path shows the same defect, report
  it in your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. The full suite is green at every step:
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (never the system python: no aiohttp).

## Files

[`modules/operator/module.yaml`, `modules/operator/__init__.py`, `pyproject.toml`, `tests/test_operator.py`, `tests/test_examples.py`, `tests/test_manifest_presentation.py`, `tests/test_profiles.py`, `tests/test_config_ui.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P8]. P8 edits the same allowlisted test,
  `test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` (its twitch type list
  becomes 5). P9 then edits the module-name set and the count of that test on top of P8's edit. The
  explicit dependency makes this order binding rather than a convention. P8 itself depends on P7, so
  the kernel is still finished first.

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_operator.py` with a fake `_server_factory` counting opened listeners and
  handing requests to the core: AC21 (page and overlay halves: 0 listeners before `start_inputs`, a
  press from each produces a `manual` event with the stated fields and is admitted through a fake
  scheduler; after `stop_inputs` the listener is closed and presses give 0 events); AC22 (no token,
  wrong token, wrong Host, foreign Origin, unknown button, second press 1 s later → 0 events and a
  refusal count each; 2 s later accepted; no token or header value in traces, diagnostics, or a
  captured `logging` record); AC23 (five diagnostics naming the field, 0 listeners; a profile without
  `operator` opens 0); AC24 (default policy accepts `manual`; `button{[hype]}` accepts `hype`,
  rejects `other` with `rejected:button_no_match`; no `streamerbot` enabled). Allowlisted count
  adaptations 19→20 in `test_examples.py` (module-name set + count), `test_manifest_presentation.py`,
  `test_profiles.py` (both tests), `test_config_ui.py` (`test_ac15_...`); each changes only the
  constant and adds `operator` to the expected set.

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

The token in a page URL could leak via `Referer` — pages set `Referrer-Policy:
  no-referrer`. aiohttp's default access log would log the query string (token) — disabled
  explicitly and asserted (AC22). The scheduler resolution must reuse watch's service pattern
  (memory: `core/main.py` builds `scheduler=None`, brain publishes `("admission", "runs")`), or
  presses are dropped in the assembled runtime. Counts change in two steps (20 here, 21 in P11); the
  allowlist names 19→21 and the intermediate value is the same adaptation, flagged in the report.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase8): ...`, `fix(phase8): ...`, `test(phase8): ...`, `refactor(phase8): ...`, `docs(phase8): ...`).
  The scope MUST be `phase8`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase8)`.
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
