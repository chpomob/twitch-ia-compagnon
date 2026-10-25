# P10 — CI job `container-trial` (R7, AC23 CI part)

Plan: `docs/campaigns/phase10/plan.md`, step P10. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [.github/workflows/ci.yml, tests/test_container.py]
- **Description:** Add one job `container-trial` (`runs-on: ubuntu-latest`,
  timeout ~40 min, `permissions` inherited read-only): checkout, setup-python 3.12,
  `python -m pip install -e ".[test]"`, step "Container trial" running
  `TWITCH_IA_COMPAGNON_CONTAINER_TRIAL=1 TWITCH_IA_COMPAGNON_BUILD_CONTEXT=. python -m
  pytest tests/test_container.py -q -p no:cacheprovider -rs` (variables via the
  step's `env:`). The existing `tests` matrix job is byte-unchanged; it already runs
  the ordinary checks with the trial nodes skipped.
- **Dependencies:** [P9]
- **Tests:** Static ordinary check (PyYAML): jobs are exactly the existing `tests` plus
  `container-trial`; `tests`'s steps equal those committed at the brief's HEAD
  (`ff4f523`), read with `git show` only if git is available without a subprocess
  — otherwise compare against a literal copy of the steps list in the test; the
  trial step's command and env hold the invocation with `1` and `.`; no step text
  contains `docker push`, `docker login`, `docker tag`, `git tag`, `gh release`
  or a release action.
- **Risks:** Comparing against `git show` would start a process (AC33) — use the
  literal-copy approach. GitHub runners have Docker and Compose v2 preinstalled; if
  `docker compose` is missing the job fails loudly, which is the intended policy.

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
