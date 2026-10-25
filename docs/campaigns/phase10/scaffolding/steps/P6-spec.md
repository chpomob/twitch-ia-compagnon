# P6 — Route-class invariance and credential precedence, socket-free (R1, R5, AC5, AC18, AC6)

Plan: `docs/campaigns/phase10/plan.md`, step P6. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [tests/test_container.py]
- **Description:** Tests only, no product change expected. AC5: for every
  combination of `--token-file`, `--no-browser`, `--start-runtime` and a
  non-loopback bind (16 combinations), build the UI through `main`'s settings path
  and drive the route table in-process: each class-(iii) route with accepted Host
  and no session gets the same refusal as the default configuration; `GET
  /instance` with token and no cookie → 200, keys exactly `marker`, `version`, no
  `Set-Cookie`; without token → 401; `GET /?token=…` → redirect setting the session
  cookie, no body. AC18: with a credential both inherited from the environment and
  stored in the credential file under the same name, the environment built for the
  supervised child (existing builder, called directly) holds the stored value.
  Then run the full suite `python -m pytest tests/ -q -p no:cacheprovider` (AC6).
- **Dependencies:** [P5]
- **Tests:** As described; the reference refusals are computed from a default
  `UISettings` in the same test, not hard-coded.
- **Risks:** Driving the route table in-process must use the existing test harness
  (aiohttp test utilities without a real socket, as test_console does) or the AC33
  guard fails. If a combination changes a refusal, that is a product bug in P2–P4,
  fixed there, never by loosening this test.

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
