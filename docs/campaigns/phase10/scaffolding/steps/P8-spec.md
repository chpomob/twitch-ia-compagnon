# P8 — compose.yaml with its static contract (R4, R5, R6, A1, A4, AC14)

Plan: `docs/campaigns/phase10/plan.md`, step P8. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [compose.yaml, tests/test_container.py]
- **Description:** One service `companion` with `build: { context:
  "${TWITCH_IA_COMPAGNON_BUILD_CONTEXT:-https://github.com/chpomob/twitch-ia-compagnon.git#main}" }`,
  `image: twitch-ia-compagnon:local`, `init: true`, `restart: unless-stopped`,
  `ports: ["127.0.0.1:8765:8765"]`, `volumes: ["companion-data:/data"]`,
  `healthcheck: { test: ["CMD", "twitch-ia-compagnon-config-ui", "--health"],
  interval, timeout, retries, start_period }`, commented optional `env_file:` /
  `environment:` credential entries and a commented optional `secrets:`
  token-file block (`--token-file /run/secrets/companion_token`); top-level
  `volumes: { companion-data: {} }`. Nothing Desktop-specific.
- **Dependencies:** [P7]
- **Tests:** AC14 (PyYAML parse): exactly 1 service and 1 named volume; mount at
  `/data`; port string `127.0.0.1:8765:8765`; `init` is `True`; `restart` present;
  healthcheck test contains `--health`; build context equals the exact
  `${…:-…#main}` string; raw text has no `host.docker.internal`, no `extensions`,
  no `x-docker-desktop`; no `extends`/`include` key (requires no other file).
- **Risks:** Commented optional blocks must not be parsed as keys (they are YAML
  comments). If the repository is private, the Git-context default fails for
  others — documented in P11 per A1, not worked around. Interval/start-period
  values constrain AC21's "within 3 intervals"; choose interval 30 s, start period
  90 s (> the 60 s Apply window) and keep them identical in Dockerfile and compose.

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
