# P7 — Dockerfile and .dockerignore with their static contracts (R3, R5, AC11, AC17 `.dockerignore` part)

Plan: `docs/campaigns/phase10/plan.md`, step P7. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [Dockerfile, .dockerignore, tests/test_container.py]
- **Description:** `Dockerfile`: stage 1 `FROM python:3.12-slim@sha256:<digest> AS
  build` copies only the files the wheel needs, by name — `COPY pyproject.toml
  LICENSE ./`, `COPY core ./core`, `COPY modules ./modules` (never `COPY . .`) — and
  runs `python -m pip wheel . --no-deps --wheel-dir /wheels`; stage 2 `FROM python:3.12-slim@sha256:<same digest>` (pinned by tag and
  digest; the digest resolved once with `docker buildx imagetools inspect` and
  written literally), `COPY --from=build /wheels /wheels`, `pip install
  --no-cache-dir /wheels/*.whl && rm -rf /wheels`, creates group/user `10001`
  with home `/data`, `mkdir -m 0700 /data && chown 10001:10001 /data`, `ENV
  XDG_CONFIG_HOME=/data HOME=/data PYTHONUNBUFFERED=1`, `VOLUME /data`, `EXPOSE
  8765`, `USER 10001`, `HEALTHCHECK --interval=… --timeout=10s --start-period=…
  CMD ["twitch-ia-compagnon-config-ui", "--health"]`, `CMD
  ["twitch-ia-compagnon-config-ui", "--host", "0.0.0.0", "--allow-non-loopback",
  "--no-browser", "--start-runtime"]` (A2). No `ENV`/`LABEL`/`ARG` holding a secret;
  no repository directory copied into stage 2. `.dockerignore` (finding P2: no
  operator file may enter **any** layer, build stage included) is an allowlist:
  first `*` (exclude everything), then re-include only `!pyproject.toml`,
  `!LICENSE`, `!core/`, `!modules/`; then, after the re-includes so they win
  inside `core/` and `modules/` too, the deny patterns: `.git`, `.venv`, `venv`,
  `**/__pycache__`, `.pytest_cache`, `*.egg-info`, `build`, `dist`,
  `**/profile.yaml`, `**/*.local.yaml`, `**/*.status.json`,
  `**/*.credentials.json`, `**/*.instance.json`, `**/*.supervisor.json`,
  `**/*.wizard.json`, `**/*.feed*`, `**/.env*`, `docs/runs/`, `findings.json`.
  The bundled example profiles (`core/config_ui/profiles/*.json`) match none of
  these and stay in the context (exempt per R5). The R6 instance record (access URL
  and token) and the supervision record are therefore excluded both by the
  allowlist and by name.
- **Dependencies:** [P6]
- **Tests:** AC11 static: exactly 2 `FROM` lines; the final matches
  `python:3\.(1[0-3])[^@]*-[a-z0-9]+@sha256:[0-9a-f]{64}`; final stage has a
  `COPY --from=<first stage>` of the wheel directory and a `pip install` of a `.whl`,
  and no `COPY . ` / `COPY <repo dir>`; final `USER` is `10001`; default `CMD` equals
  A2's argument list; `HEALTHCHECK` runs `--health`; stage 1's `COPY` sources are
  exactly `pyproject.toml`, `LICENSE`, `core`, `modules` (no `.` source in any
  stage). AC17 part: `.dockerignore`
  contains each required pattern; finding P2 — `.dockerignore`'s first pattern is
  `*`, its only `!` re-includes are the four paths above, and it lists
  `**/profile.yaml`, `**/*.instance.json`, `**/*.supervisor.json`,
  `**/*.wizard.json` after them; a pure-Python evaluator of the `.dockerignore`
  rules (last match wins, `**` and `!` per Docker's documented semantics, run over a
  synthetic path list, no Docker daemon) judges excluded: `profile.yaml`,
  `config/profile.yaml`, `core/profile.yaml`, `profile.instance.json`,
  `core/x.instance.json`, `profile.supervisor.json`, `profile.local.yaml`,
  `profile.credentials.json`, `profile.status.json`, `.env`, `.env.local`,
  `docs/runs/x.md`, `.git/config`; and included: `pyproject.toml`, `LICENSE`,
  `core/config_ui/__init__.py`, every `core/config_ui/profiles/*.json`,
  `core/config_ui/strings/en.json`, `modules/twitch/module.yaml`; `ENV`/`LABEL` names match none of the credential
  names of the four profiles (read from `core/config_ui/profiles/*.json`) nor
  `*_KEY`, `*_TOKEN`, `*_SECRET`, `*PASSWORD*`.
- **Risks:** The wheel build needs the setuptools backend in stage 1 (pip installs
  it into its isolated build env — needs network at build time, which a Git-context
  build has). Package data (profiles, manifests, strings) must be in the wheel —
  verified for real in P12 (AC22). A `.dockerignore` pattern too broad (e.g. `*.json`)
  would drop shipped profiles — keep patterns exact; the evaluator test pins the
  bundled profiles and strings as included. The allowlist means any new top-level
  file the wheel build later needs must be re-included explicitly (the build fails
  loudly, which P12's real build catches); `pyproject.toml` declares no `readme`
  today, so `README.md` is not needed. The in-test evaluator is a model of Docker's
  rules, not Docker itself — P12's AC17 image inspection (0 `profile.yaml`/
  `*.instance.json` files) is the real check. Non-trivial: irreversible-ish
  artefact contract (base digest, uid).

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
