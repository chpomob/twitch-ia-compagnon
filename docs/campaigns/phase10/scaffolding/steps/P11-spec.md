# P11 — The self-hosting guide pair and the documentation links (R8, R5 precedence, AC24, AC25 link parts, AC18 guide part)

Plan: `docs/campaigns/phase10/plan.md`, step P11. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [docs/self-hosting.md, docs/self-hosting.fr.md, docs/README.md, docs/installation.md, docs/installation.fr.md, tests/test_docs_bilingual.py, tests/test_container.py]
- **Description:** English normative guide with `##` sections: Quick start (at most
  3 commands: obtain `compose.yaml` with `curl -fsSLO <raw URL>`, `docker compose up
  -d`, `docker compose logs companion`; needs `git` on the host; first `up` builds;
  private-repository caveat, A1), The compose file line by line, The `/data` volume
  (every AC7 file by name under `/data/twitch-ia-compagnon/`: `profile.yaml`, the
  overlay, `profile.status.json`, the feed, `profile.wizard.json`, the credential
  file, `profile.instance.json`, `profile.supervisor.json` — exact names taken from
  the code; bind-mount ownership uid 10001 and mode, A3), First run and restore,
  Environment variables and the credential file (stored overrides inherited, R5),
  The token (printed once per start, `--token-file`, `--allowed-host`, exposure
  beyond the host), The four profiles (and A7), Backup, restore and upgrade,
  Healthcheck states (`ready`, `degraded`, `starting`, `idle`, `unhealthy` and the
  exact lines), Image size (placeholder line filled in P12), Limits (no desktop
  session, no browser opener, no launcher, no microphone, no native bundle). Every
  R1 option named (`--no-browser`, `--token-file`, `--start-runtime`, `--health`,
  `--allow-non-loopback`, `--host`). French translation with the same `##` count
  and code identifiers. `docs/README.md`: link both files and a line that phase 10
  adds the image, compose file and guide. `docs/installation.md` /
  `.fr.md`: one paragraph each linking the guide of its language. Add
  `"self-hosting"` to `GUIDES`.
- **Dependencies:** [P10]
- **Tests:** `tests/test_docs_bilingual.py` passes with `self-hosting` declared
  (same sections and identifiers). Ordinary checks in `tests/test_container.py`:
  the English guide names every AC7 file, every R1 option, the five A6 states,
  `10001` and each limit; both guides state the precedence (AC18 guide part); the
  README links both files; each installation guide links its language's guide;
  quick-start block has ≤3 commands, none `git clone`/`docker build`. Run
  `tests/test_installation_doc.py` and `tests/test_phase5_docs_status.py` unchanged.
- **Risks:** Existing doc guards (installation-doc tests) may assert exact paragraph
  sets — if one breaks, stop and flag rather than edit it. Code identifiers matched
  by `_TOKEN` must appear identically in both languages; do not translate
  backticked names. The size line must exist in a parseable form (`NNN.N MB`) for
  AC13's agreement check.

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
