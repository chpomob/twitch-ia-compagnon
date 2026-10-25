# P3 — The supervision record and its collision rule (R6 item 3, AC19 supervision and collision parts)

Plan: `docs/campaigns/phase10/plan.md`, step P3. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [core/config_ui/__init__.py, tests/test_container.py]
- **Description:** Add `supervision_path(settings)` → `<base>.supervisor.json` beside
  the profile (same derivation as `<base>.instance.json`). Add atomic writer
  (temp file, owner-only mode 0600, `os.replace`) that runs under the instance
  record's lock, writing `{"version": 1, "marker": …, "state": …}` plus `pid` and
  `launched_at` (status-record timestamp format) when `launched`. `ConfigUI` gets a
  `_supervision: Path | None = None` set only by `main`'s claim path right after a
  successful claim (publish `not_started`, replacing any record present). Hooks:
  every place `Supervisor.start` returns a child (console Start, Apply's restart,
  later `--start-runtime`) writes `launched`; the console Stop whose outcome is
  `stopped` for this UI's child writes `stopped` (Apply's internal stop does not).
  With `_supervision is None` nothing is written. Release (the existing
  `_remove_instance_if` call site) also removes the supervision record when its
  `marker` equals this UI's, whether or not the instance record is still ours; a
  record with another marker is left byte-identical. `startup_checks` adds the
  supervision path to the collision set, checked after all existing members,
  against base profile, overlay, status record, feed path, wizard state, credential
  file and instance record (canonical form / same file, the existing helper); a
  hit yields exactly one problem `supervision_path_collision` naming the role
  "supervision record", its path and the colliding file's role and path, never
  contents.
- **Dependencies:** [P2]
- **Tests:** AC19 (socket-free): after `main([], serve=<fake>)` claims, the record
  reads `not_started` with this marker; console Start (fake supervisor child) →
  `launched` with child pid and a timestamp; Apply restart → `launched` again;
  console Stop → `stopped`; Apply's stop leaves it `launched`; release removes it,
  including when the instance record was replaced by another mid-serve; a
  foreign-marker record is byte-identical after release; a directly built
  `ConfigUI` writes none on Start/Stop. Collision: status path, overlay path
  (configured as phase 9's AC1 does) and feed path (via its environment variable)
  each set to `<base>.supervisor.json` with a fixture content → `startup_checks`
  returns exactly `[supervision_path_collision …]`, fixture content absent;
  `main` exits non-zero, `serve` 0 calls, file byte-identical; default location →
  0 problems. Re-run the spec's "Exact-directory assertions" tests unedited
  (`tests/test_first_run.py`, `tests/test_config_ui.py`, `tests/test_console.py`).
- **Risks:** Removal at release must happen even on exception paths already
  handled by the instance release (`finally`), or a crash leaves a stale record —
  harmless for health (marker mismatch → row 6/7) but must not break the
  exact-directory tests that assert `== []` after a clean exit. Writing under the
  instance lock from the event-loop thread must not block on a lock held by a
  concurrent launch; keep the write short. Non-trivial: new file format, ordering
  invariant (published after claim, removed before/at release).

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
