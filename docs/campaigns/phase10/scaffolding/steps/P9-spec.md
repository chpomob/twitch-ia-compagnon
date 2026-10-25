# P9 — The five container trial nodes, the file-shape check, and the AC33 registrations (R7, AC23 default parts)

Plan: `docs/campaigns/phase10/plan.md`, step P9. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [tests/test_container.py, tests/conftest.py, tests/test_ac33_isolation.py]
- **Description:** Add `CONTAINER_TRIAL_VARIABLE = "TWITCH_IA_COMPAGNON_CONTAINER_TRIAL"`
  and import phase 2's `required` gate (the same helper `test_phase2_trials.py`
  uses). Five nodes, each starting with `required(CONTAINER_TRIAL_VARIABLE)`:
  `test_trial_container_image` (AC12, AC13 measurement written to a trial
  artefact under `docs/runs/`, AC17 image part; plus, for finding P2, the build
  stage itself: `docker build --target build` of the same context into a
  throwaway tag, then `docker run --rm` of that tag listing its filesystem, which
  must hold 0 files named `profile.yaml`, `*.instance.json`, `*.supervisor.json`,
  `*.credentials.json`, `*.local.yaml` or `.env*` — run over a context into which
  the node writes decoy `profile.yaml`, `core/profile.yaml` and
  `x.instance.json` files: a temporary directory (`tmp_path`) holding a copy of the
  repository's tracked files (`git ls-files` of the checkout) plus the decoys, so
  the exclusion is exercised, not assumed), `test_trial_container_volume`
  (AC7, AC8, AC9), `test_trial_container_secrets` (AC16),
  `test_trial_container_health` (AC21), `test_trial_container_profiles` (AC22, proof
  code piped into the container's own interpreter via `docker compose exec -T …
  python -` from a string in the test file, never baked). Shared helpers: locate
  `docker` with `shutil.which` → `pytest.fail("docker executable not found")` when
  missing; daemon check and build failures → `pytest.fail` naming the cause; each
  node uses a unique compose project name (`-p`), `TWITCH_IA_COMPAGNON_BUILD_CONTEXT`
  from the environment, waits only via `docker compose up --wait --wait-timeout`,
  `docker events --until`, and subprocess `timeout=`; teardown `docker compose down
  -v --remove-orphans` in `finally`. Fixture parameters and 24-char sentinels per
  credential name of the four profiles. File-shape ordinary check: the module has
  exactly those five `test_trial_` nodes, each's first statement is
  `required(CONTAINER_TRIAL_VARIABLE)` (AST), and the source contains no `skipif`,
  `xfail`, `importorskip` or `pytest.skip(`. `tests/conftest.py`: add
  `"test_container.py": ("required", {five node names → "CONTAINER_TRIAL_VARIABLE"})`
  to `_TRIAL_GATES`, nothing else. `tests/test_ac33_isolation.py`: import
  `test_container`, add it to `TRIAL_FILES`, change `assert len(nodes) == 20` to
  `== 25`; extend `_switches` only if its existing branches cannot read the new
  file's constant (it reads constants from the file's own tables — verify).
- **Dependencies:** [P8]
- **Tests:** Default run `python -m pytest tests/test_container.py -q -p
  no:cacheprovider -rs` → exactly 5 skipped with reason
  `TWITCH_IA_COMPAGNON_CONTAINER_TRIAL is unset`, everything else passes. An
  ordinary check runs each trial node's body with the variable set and `shutil.which`
  patched to `None` (monkeypatched module attribute, no process) and asserts
  `pytest.fail.Exception` whose message names `docker` (AC23 "fails, not skips").
  `tests/test_ac33_isolation.py` passes with count 25.
- **Risks:** The AC23 "docker unfindable" check must not start a subprocess before
  the `which` check, or the AC33 guard trips. The ordinary check that invokes trial
  bodies must itself not be named `test_trial_*`. `_ac33_isolation` exempts a trial
  node only when opted in — confirm the gate name `"required"` matches the attribute
  imported into `test_container`. The skip reason string comes from `required`;
  verify it renders exactly `… is unset`.

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
