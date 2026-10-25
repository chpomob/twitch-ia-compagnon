# P5 — The health command (R6 rows 1–13, A6, AC19 rows, AC20)

Plan: `docs/campaigns/phase10/plan.md`, step P5. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [core/config_ui/__init__.py, tests/test_container.py]
- **Description:** `--health` option in `from_argv` (same `--config`/default
  location resolution). `main` with `health=True` runs `health(settings, seams)`
  before any startup check, claim or serve, prints exactly one line and returns the
  row's exit status. `HealthSeams` dataclass (defaults looked up at call time):
  `probe(port, url, timeout)` → status code/JSON or "no answer" (default: one
  `GET /instance?token=…` to `127.0.0.1:<recorded port>`, connecting only to
  `127.0.0.1`, no cookie jar, total deadline 5 s), `liveness` + `started_at` (the
  P1 rule, `launched_at` as publication time), `clock` (wall clock, for the 60 s
  Apply window `DEFAULT_APPLY_WINDOW`), readers for instance record (existing
  reader), supervision record (P3 format, strict validation), status record
  (existing classifier) and landing decision. Rows evaluated in order 1→13 exactly
  as the spec's table; the printed line is one of the fixed strings, `degraded:`
  followed by sorted comma-separated module names. No token, URL, session or value
  in any line; the token is taken from the instance record's access URL only to
  build the probe request.
- **Dependencies:** [P4]
- **Tests:** AC19: one socket-free case per row 1–13 against fake records, fake
  probe, fake liveness and an injected clock (row 2 twice: refused, silent);
  overlapping cases take the earlier row (e.g. probe 401 + unusable supervision →
  row 3); line equality and exit status asserted; the fake probe receives the
  record's token and no cookie; the line contains no token. AC20: the default probe
  builder's target address is `127.0.0.1` (asserted on the request it would send
  via an injected connector, no socket) and, against a never-answering fake with an
  injected clock, returns within 5 s of that clock → `unhealthy: not answering`.
- **Risks:** Windows has no access URL in the instance record (phase 9 persists the
  token only on POSIX) → row 1 "incomplete" on Windows; acceptable (the image is
  Linux) but document it in the docstring. aiohttp client sessions create cookie
  jars — use `aiohttp.DummyCookieJar` or a raw `http.client` call with a timeout so
  no session cookie is ever stored. Module names in `degraded:` come from the status
  record; ensure they are names only, never values.

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
