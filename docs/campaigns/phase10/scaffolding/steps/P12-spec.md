# P12 — Run the container trial and fill the trial record and measured size (R3, R4, R5, R7, R8; AC7–AC9, AC12, AC13, AC15, AC16, AC17 image part, AC21, AC22, AC25 record part)

Plan: `docs/campaigns/phase10/plan.md`, step P12. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [docs/campaigns/phase10/trial-record.md, docs/self-hosting.md, docs/self-hosting.fr.md, tests/test_container.py]
- **Description:** Write the trial-record template (who, host, Docker and Compose
  versions, commit, exact commands typed, timestamps, measured image size in bytes,
  healthcheck states observed, first run, restart over the restored volume, the five
  trial nodes' outcomes, outcome, limits). Run R7's invocation on a Linux host with
  Docker Engine + Compose v2; fix any product defect found in the step that owns it
  (P1–P8) and re-run. Perform the operator trial: from an empty directory, obtain
  `compose.yaml`, `docker compose up -d`, read the URL from logs, complete wizard
  screens 1–5 in a browser (AC15), restart with `docker kill` + `up -d` (AC9). Fill
  the record; write the measured size into both guides in MB to 1 decimal. Add the
  ordinary check for AC13's agreement (guide MB vs record bytes within 5 %) and for
  the record holding every target-list field with a filled outcome (AC25).
- **Dependencies:** [P11]
- **Tests:** The five trial nodes pass with the variable set; the default run still
  reports 5 skips; AC13/AC25 ordinary checks pass; full suite green.
- **Risks:** Needs a host with Docker — if unavailable in the dev loop, the step
  cannot be completed and must say so rather than fabricate a record (no invented
  sizes or outcomes). AC21 timing depends on the healthcheck interval chosen in
  P7/P8. AC7's "files modified after start" listing must exclude the pip/Python
  cache paths — `PYTHONDONTWRITEBYTECODE=1` in the image avoids `__pycache__` writes;
  if a write lands outside `/data`, that is an R2 defect to fix, not to exclude.
  The Git-context default needs the branch pushed for the operator trial from a
  directory without a checkout (A1).

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
