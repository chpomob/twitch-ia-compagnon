# P13 — Full-branch review gate (all requirements R1–R8, AC1–AC25)

Plan: `docs/campaigns/phase10/plan.md`, step P13. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [core/config_ui/__init__.py, Dockerfile, .dockerignore, compose.yaml, .github/workflows/ci.yml, tests/test_container.py, tests/conftest.py, tests/test_ac33_isolation.py, tests/test_docs_bilingual.py, docs/self-hosting.md, docs/self-hosting.fr.md, docs/README.md, docs/installation.md, docs/installation.fr.md, docs/campaigns/phase10/trial-record.md]
- **Description:** Review `git diff ff4f523...HEAD` as one change, not commit by
  commit. Per-commit reviews miss cross-file bugs by construction: each step can be
  correct in isolation while the set is broken where files meet — e.g. the
  Dockerfile's `HEALTHCHECK` interval vs compose's vs AC21's three-interval bound;
  the `CMD` options (P7) vs their parser (P2/P4); the supervision record's file name
  (P3) vs the guide's file list (P11) vs the AC7 listing (P12); `--token-file`'s
  token vs what the instance record persists vs what `--health` presents (P2/P5);
  `_TRIAL_GATES` (P9) vs the gate constant name in `test_container.py`. Check the
  spec's allowlist: no test file outside it edited, no exact-directory assertion
  adapted, no relaxed assertion; no secret/token in any log or health line; every
  new option defaults off; spec target list ⇔ changed files.
- **Dependencies:** [P12]
- **Tests:** `python -m pytest tests/ -q -p no:cacheprovider` (default, 5 skips in
  `test_container.py`, all else green); R7's trial invocation green on a Docker
  host; `git diff --stat ff4f523...HEAD` matches the spec's target list plus
  `spec.md`/`plan.md`.
- **Risks:** A cross-file mismatch found here belongs to the owning step's fix
  round; the gate does not patch silently.

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
