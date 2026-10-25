# P4 — `--start-runtime` (R1 d, AC4)

Plan: `docs/campaigns/phase10/plan.md`, step P4. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [core/config_ui/__init__.py, tests/test_container.py]
- **Description:** `UISettings.start_runtime: bool = False`, `--start-runtime` in
  `from_argv`. In `ConfigUI.on_listening` (the hook that runs once listening, now
  on its own thread), when the option is set: compute the landing decision the
  access URL uses; when it is console or editor (profile present, no unfinished
  wizard), call the same Start entry the console's Start route uses (same
  refusals: foreign live status-record pid, already running, etc.), which also
  writes the `launched` supervision record (P3); otherwise start nothing and log
  one value-free reason line (`start-runtime: wizard unfinished` / `no profile`).
  A refusal is logged value-free and the UI keeps serving. Runs exactly once per
  UI start.
- **Dependencies:** [P3]
- **Tests:** AC4: completed wizard state → fake supervisor `start` called exactly 1
  time, after the fake listening signal; unfinished wizard and no profile → 0 calls
  and a reason line with no value; fake live process holding the status record →
  refused with the console Start's refusal; option absent → 0 calls. Supervision
  record shows `launched` after the AC4 success case (AC19's `--start-runtime` part).
- **Risks:** `on_listening` also drives the opener; ordering must keep the access
  URL print first and the opener behaviour of P2. The Start path may expect a
  session/CSRF context — call the underlying method, not the HTTP route, so no
  route class changes (R1). Running Start off the event-loop thread must use the
  same lock the console route uses.

## Standing phase-10 decisions that override any contrary reading of a requirement:

- **No new runtime dependency and no second image.** The image installs the project's own
  distribution; whatever the container needs beyond it is a build-time tool, not a runtime one.
- **Secrets are supplied, never baked.** No image layer, environment variable, label or default
  configuration may carry a credential, a key, a token or one of the operator's profiles. The
  `${...}` reference mechanism stays the only way a value enters a configuration. A hidden file
  (`.env`, `*.instance.json`, `*.supervisor.json`, anything under the state directory) must never
  reach the build context.
- **The ordinary suite stays green with no Docker daemon.** Checks that read files (the Dockerfile's
  declared properties, the compose file's shape, `.dockerignore`'s coverage) are ordinary tests.
  Anything that needs a daemon is a trial check: it runs where a daemon exists (this machine, the
  `container-trial` CI job) and the ordinary suite neither skips silently nor pretends it ran. A new
  exemption follows phase 9's Amendment A2 terms: one named class, enumerated, asserted by an
  invariant test.
- **`/instance` stays session-free** (token, no session, marker and version only), exactly as phase 9
  defined it; every other route requires a session. The container's healthcheck uses the probe.
- **One volume for all state**, documented file by file, and a restored volume must not change
  behaviour silently.
- **A state the health command cannot compute from the records it reads is not one of its states.**
  Remove it or make it observable — never guess.
- **Nothing is published**: no registry push, no tag, no release, no GitHub release. Building,
  running and measuring locally is the whole phase.
- Never weaken an existing test to make a step pass. If an existing test must change, the plan names
  it, the change is an added expectation (an allowlist entry), and silently dropping an assertion is
  a rejection.

Loop constraints: run `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` before committing;
one atomic commit per step; no `git push`; no edit to `docs/campaigns/phase10/{spec,plan}.md`.
