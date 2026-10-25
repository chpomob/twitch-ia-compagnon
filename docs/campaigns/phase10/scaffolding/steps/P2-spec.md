# P2 — `--no-browser`, `--token-file` and the non-loopback warning (R1 a–c, AC1, AC2, AC3)

Plan: `docs/campaigns/phase10/plan.md`, step P2. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [core/config_ui/__init__.py, tests/test_container.py]
- **Description:** Extend `UISettings` with `no_browser: bool = False` and
  `token_file: Path | None = None`; `UISettings.from_argv` gains `--no-browser` and
  `--token-file PATH` (help strings value-free). Token file: read bytes, decode
  UTF-8, strip surrounding whitespace, accept only `^[A-Za-z0-9_-]{43,128}$`
  (A5); a missing/unreadable, empty or invalid file makes `main` exit 2 before any
  claim or serve, with one stderr diagnostic naming the path and the rule broken
  (`unreadable`, `empty`, `length 43–128`, `alphabet A–Z a–z 0–9 - _`) and never the
  content. The validated token replaces `secrets.token_urlsafe(32)` in `ConfigUI`
  (constructor parameter defaulting to `None` → generated, as today); the same
  token checks of both route classes apply unchanged. `--no-browser`: `main` and
  `claim_instance` skip the opener call (both the own-URL call and the
  "open the running instance" path) while the access URL is still printed once.
  Warning: when the bind host is not loopback (wildcard `0.0.0.0`/`::` included,
  decided by the existing loopback test used by `--allow-non-loopback`), `main`
  writes exactly one line to stderr before serving, naming host and port and saying
  the UI is reachable from the network and protected only by the access token; the
  line is built from host and port only.
- **Dependencies:** [P1]
- **Tests:** AC1: wildcard + `--allow-non-loopback` → exactly 1 warning line on
  captured stderr before the fake `serve` is called, containing `0.0.0.0`, the port
  and `token`, not containing the token; `127.0.0.1` → 0 such lines. AC2: injected
  opener call count 0 with `--no-browser`, 1 without; access URL printed exactly 1
  time in both. AC3: valid 43-char token + `\n` → access URL ends `?token=<43 chars>`
  and a request via the existing in-process handler test helpers (no socket)
  presenting it obtains a session; missing, empty, 42-char, 129-char and `+`-bearing
  files → exit 2, `serve` called 0 times, stderr names the path and not the content
  (each content is a distinct fixture string asserted absent).
- **Risks:** `claim_instance` opens an already-running instance via the opener —
  `--no-browser` must cover that branch too, or a second container start would try
  to open a browser. Token from a file must also be what the instance record
  persists on POSIX (the health probe reads it there). Help-text or diagnostic
  strings may be swept by existing redaction/i18n guards — keep them in the same
  style as existing `--allow-non-loopback` text.

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
