---
spec: "phase10-distribute"
version: "1.0"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: true
---

# Implementation Plan

Phase 10 ships one image, one compose file, a health command and a self-hosting
guide pair. Product code changes are confined to `core/config_ui/__init__.py`
(four opt-in options, the supervision record, the stale-record rule, the health
command). Every ordinary check lives in the new `tests/test_container.py`, which
is created in P1 and grown by each later step; the five Docker trial nodes are
added in P9 and executed for real in P12.

Standing constraints for every step (from the spec and the repository's guards):

- No default behaviour changes (AC6): every new `UISettings` field defaults off;
  `/instance`'s phase-9 contract is untouched (R1 route classes i–iii).
- Ordinary checks open no socket and start no process (AC33 guard); they drive
  fakes, injected clocks and `InstanceSeams`-style seams.
- No positive `sleep` in tests (the suite's existing check); trial waits only
  through Docker's bounded waits and subprocess timeouts.
- No test outside the R7/AC6 allowlist (`tests/conftest.py` `_TRIAL_GATES`,
  `tests/test_ac33_isolation.py` `TRIAL_FILES` + count, `tests/test_docs_bilingual.py`
  `GUIDES`) is edited; none of the "Exact-directory assertions" of the spec is
  adapted. If a step finds one of them breaking, it stops and flags it rather
  than editing the test.
- No diagnostic, warning, reason line or health line holds a token, session or
  credential value (R1, R5, R6).

## Steps

### P1: Stale-record rule — a live pid that started after the record is not its publisher (R2, AC10)
- **Files:** [core/config_ui/__init__.py, tests/test_container.py]
- **Description:** Add a process start-time reader `process_started_at(pid) -> float | None`
  (wall-clock UTC epoch seconds): on Linux read field 22 (`starttime`, clock ticks
  since boot) of `/proc/<pid>/stat` divided by `os.sysconf("SC_CLK_TCK")`, added to
  a boot time taken as `time.time() - <first field of /proc/uptime>` (both read in
  one call; centisecond precision — not `btime` of `/proc/stat`, which is whole
  seconds and would shift a start up to 1 s earlier, letting a pid reused within
  that second pass as the publisher); on any other platform, or on any
  `OSError`/parse failure, return `None`. Add a judgement helper `record_pid_live(pid, published_at,
  liveness, started_at)` that returns `liveness(pid)` unchanged when the pid is not
  live or `started_at(pid)` is `None`, and returns `False` whenever the live process
  started strictly after `published_at` — `started_at(pid) > published_at`, with
  **no tolerance or grace interval** (finding P1: R2 and AC10 admit no exception, so
  a pid reused 0.5 s or 1 ms after publication is judged not live). Granularity
  cannot wrongly stale a genuine publisher: a publisher necessarily started before
  it wrote its record, and tick truncation of `starttime` only moves its computed
  start earlier, never later.
  Wire it into the two judgements R2 names: (a) the instance-record stale judgement
  in the claim path (publication time = the record file's `st_mtime`, the record
  format unchanged); (b) the status-record "foreign live pid" refusal used by
  Start (publication time = the status record's own timestamp field, read by the
  existing reader, falling back to `st_mtime`). Add an injectable
  `started_at: Callable[[int], float | None]` to `InstanceSeams` (default looked up at
  call time, like the other seams) and the equivalent seam on the Start-refusal
  path, so existing tests that inject `liveness` keep their results (their pids are
  judged with `started_at` defaulting to the real reader, which for a fake pid
  yields `None` or a time before the record → unchanged; where that is not
  guaranteed, the default seam is applied only when liveness is the default, see
  Risks). Create `tests/test_container.py` with a module docstring describing the
  ordinary/trial split of R7 and the first ordinary checks.
- **Dependencies:** []
- **Tests:** In `tests/test_container.py` (socket-free): AC10 — instance record and
  status record whose pid is live (fake liveness) with `started_at` later than
  publication → instance record removed as stale and `main([], serve=<fake>)`
  serves once; Start is not refused by the status record. Same records with
  `started_at` earlier → judged as today (refused/opened as in phase 9). With
  `started_at` returning `None` → unchanged judgement. Boundary cases of the
  "strictly after, no tolerance" rule (finding P1), each on both the instance-record
  path (record `st_mtime` set with `os.utime`) and the status-record path:
  `started_at = published_at + 0.5` → not live; `published_at + 0.001` → not live;
  `published_at + 1.5` → not live; `started_at == published_at` → live (judged as
  today); `published_at - 0.001` → live. A static check asserts the helper module
  defines no tolerance/grace constant applied to the comparison (the comparison is
  `>` on the raw values). Unit test of the `/proc`
  parser against fixture strings (including a `comm` containing spaces and `)`),
  read through an injected reader, no real `/proc` read; the reader computes boot
  time from injected `/proc/uptime` text and an injected clock (fractional seconds
  kept, e.g. uptime `"1234.56 99.0"`, clock `10000.25`, starttime `123400` ticks at
  100 Hz → `10000.25 - 1234.56 + 1234.00`), proving sub-second precision. Run
  `tests/test_first_run.py` and `tests/test_status_record.py` unchanged (existing
  instance-record and foreign-pid tests pass, AC10's second sentence).
- **Risks:** `/proc/<pid>/stat` `comm` field may contain spaces/parentheses — parse
  from the last `)`. Clock skew between `btime` and `st_mtime` (NTP step, container
  started after a host clock jump) could mark a genuine publisher stale; no tolerance
  is added for it (finding P1 — a tolerance would let a pid reused within the window
  pass as live, which R2/AC10 forbid); a backwards clock step between publication and
  judgement is out of scope of R2 and is noted in the helper's docstring. Computing
  boot time from `/proc/uptime` and the wall clock at read time follows the current
  wall clock, consistent with `st_mtime` and the status record's timestamp. Existing tests that fake liveness for
  the test process's own pid (a real process started before the fixture record was
  written) must stay unaffected — verify by running them; if any injected-liveness
  test changes outcome, make the default `started_at` apply only when `liveness`
  is the default, and record that decision in the docstring (non-trivial: branching
  logic + invariant the type system cannot verify).

### P2: `--no-browser`, `--token-file` and the non-loopback warning (R1 a–c, AC1, AC2, AC3)
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

### P3: The supervision record and its collision rule (R6 item 3, AC19 supervision and collision parts)
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

### P4: `--start-runtime` (R1 d, AC4)
- **Files:** [core/config_ui/__init__.py, tests/test_container.py]
- **Description:** `UISettings.start_runtime: bool = False`, `--start-runtime` in
  `from_argv`. In `ConfigUI.on_listening` (the hook that runs once listening, now
  on its own thread), when the option is set: compute the landing decision the
  access URL uses; when it is console or editor (profile present, no unfinished
  wizard), call the same Start entry the console's Start route uses (same
  refusals: foreign live status-record pid, already running, etc.), which also
  writes the `launched` supervision record (P3); otherwise start nothing and log
  one value-free reason line (`start-runtime: wizard unfinished` / `no profile`).
  A refusal is logged value-free and the UI keeps serving. Runs exactly once per
  UI start.
- **Dependencies:** [P3]
- **Tests:** AC4: completed wizard state → fake supervisor `start` called exactly 1
  time, after the fake listening signal; unfinished wizard and no profile → 0 calls
  and a reason line with no value; fake live process holding the status record →
  refused with the console Start's refusal; option absent → 0 calls. Supervision
  record shows `launched` after the AC4 success case (AC19's `--start-runtime` part).
- **Risks:** `on_listening` also drives the opener; ordering must keep the access
  URL print first and the opener behaviour of P2. The Start path may expect a
  session/CSRF context — call the underlying method, not the HTTP route, so no
  route class changes (R1). Running Start off the event-loop thread must use the
  same lock the console route uses.

### P5: The health command (R6 rows 1–13, A6, AC19 rows, AC20)
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

### P6: Route-class invariance and credential precedence, socket-free (R1, R5, AC5, AC18, AC6)
- **Files:** [tests/test_container.py]
- **Description:** Tests only, no product change expected. AC5: for every
  combination of `--token-file`, `--no-browser`, `--start-runtime` and a
  non-loopback bind (16 combinations), build the UI through `main`'s settings path
  and drive the route table in-process: each class-(iii) route with accepted Host
  and no session gets the same refusal as the default configuration; `GET
  /instance` with token and no cookie → 200, keys exactly `marker`, `version`, no
  `Set-Cookie`; without token → 401; `GET /?token=…` → redirect setting the session
  cookie, no body. AC18: with a credential both inherited from the environment and
  stored in the credential file under the same name, the environment built for the
  supervised child (existing builder, called directly) holds the stored value.
  Then run the full suite `python -m pytest tests/ -q -p no:cacheprovider` (AC6).
- **Dependencies:** [P5]
- **Tests:** As described; the reference refusals are computed from a default
  `UISettings` in the same test, not hard-coded.
- **Risks:** Driving the route table in-process must use the existing test harness
  (aiohttp test utilities without a real socket, as test_console does) or the AC33
  guard fails. If a combination changes a refusal, that is a product bug in P2–P4,
  fixed there, never by loosening this test.

### P7: Dockerfile and .dockerignore with their static contracts (R3, R5, AC11, AC17 `.dockerignore` part)
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

### P8: compose.yaml with its static contract (R4, R5, R6, A1, A4, AC14)
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

### P9: The five container trial nodes, the file-shape check, and the AC33 registrations (R7, AC23 default parts)
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

### P10: CI job `container-trial` (R7, AC23 CI part)
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

### P11: The self-hosting guide pair and the documentation links (R8, R5 precedence, AC24, AC25 link parts, AC18 guide part)
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

### P12: Run the container trial and fill the trial record and measured size (R3, R4, R5, R7, R8; AC7–AC9, AC12, AC13, AC15, AC16, AC17 image part, AC21, AC22, AC25 record part)
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

### P13: Full-branch review gate (all requirements R1–R8, AC1–AC25)
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

## Findings addressed

| Finding | Step(s) | Resolution |
|---------|---------|------------|
| P1 (blocker) — tolerance lets rapid pid reuse pass as live | P1 | Tolerance removed: `started_at > published_at` on raw values; start time computed with sub-second boot time (`/proc/uptime` + wall clock, not whole-second `btime`); boundary tests at +0.5 s, +0.001 s, +1.5 s, equal and −0.001 s on both record paths |
| P2 (blocker) — build context can carry operator profiles and instance records into a build layer | P7, P9 | `.dockerignore` is an allowlist (`*` then `!pyproject.toml`, `!LICENSE`, `!core/`, `!modules/`) with `**/profile.yaml`, `**/*.instance.json`, `**/*.supervisor.json`, `**/*.wizard.json` and the AC17 patterns denied after it; stage 1 copies only those four paths by name; static evaluator test in P7; trial node in P9 builds `--target build` over a context with decoys and asserts 0 operator files |

## Caller enumeration

API surface changed: `UISettings` (new defaulted fields `no_browser`, `token_file`,
`start_runtime`, `health`), `UISettings.from_argv` (four options), `main` (health
branch, warning, opener skip), `startup_checks` (one more collision member),
`InstanceSeams` (new defaulted `started_at` seam), `ConfigUI` (optional token and
supervision path, both defaulted). Search: `grep -rn "UISettings(\|from_argv\|config_ui.main(\|InstanceSeams(\|startup_checks("`.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/config_ui/__init__.py | `UISettings.from_argv`, `main`, `startup_checks`, `claim_instance`, `ConfigUI.__init__`, `ConfigUI.on_listening`, Start/Stop/Apply handlers, `_remove_instance_if` call site | Changed in P1–P5; defaults keep behaviour |
| core/config_ui/__main__.py | `main()` | None: calls `main` unchanged |
| pyproject.toml | `twitch-ia-compagnon-config-ui = core.config_ui:main` | Unchanged; `--health` is an option of it |
| tests/test_config_ui.py | `UISettings(...)`, `config_ui.main(...)`, `startup_checks` (~38 sites) | None: new fields defaulted |
| tests/test_first_run.py | `UISettings(...)`, `main`, `InstanceSeams(...)` (~51 sites) | None: `started_at` defaulted; P1 verifies injected-liveness results are unchanged |
| tests/test_console.py | `UISettings(...)` (8 sites) | None |
| tests/test_input_ownership.py, tests/test_main.py, tests/test_status_record.py | one site each | None |
| Dockerfile, compose.yaml | `CMD` / `healthcheck` invoking the console script | New callers (P7, P8) |

Unknown callers: dynamic construction via `**kwargs` is not detected by grep;
external consumers of `core.config_ui` are not known to exist.

## Ordering rationale

Product code first, in dependency order: the stale-record rule (P1) is
independent and makes restore safe; the token/browser/warning options (P2) are
needed by the supervision record's tests and by the image's `CMD`; the supervision
record (P3) must exist before `--start-runtime` writes `launched` through it (P4),
and both before the health command reads it (P5). P6 locks the route-class and
precedence invariants after all options exist. Packaging (P7 Dockerfile, P8
compose) depends on every option its `CMD`/healthcheck names. Trial nodes and the
AC33 registrations (P9) need the Dockerfile and compose file they drive; the CI
job (P10) runs them. The guide (P11) documents the finished behaviour and file
names; the real trial (P12) measures the size and fills the record the guide and
AC13 depend on. The full-branch review (P13) comes last, over the whole diff.
