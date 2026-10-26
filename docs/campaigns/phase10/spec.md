---
name: "phase10-distribute"
version: "1.0"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec, phase10, container, image, compose, healthcheck, self-hosting]
targets:
  - file: Dockerfile
    description: "New: the one image — a build stage that produces the project's own wheel from the repository, a runtime stage on a digest-pinned Python base that installs only that wheel (and so only aiohttp, PyYAML and their own requirements), runs as numeric non-root user 10001, keeps all state under the `/data` volume, carries no secret, no profile and no key, starts the configuration UI in container mode by default and declares the health command (R1, R2, R3, R5, R6)."
  - file: .dockerignore
    description: "New: keep `.git`, virtual environments, caches, `*.local.yaml`, `*.status.json`, `*.credentials.json`, `.env*` and `docs/runs/` out of the build context, so no operator file can enter a layer (R3, R5)."
  - file: compose.yaml
    description: "New: one service, one named volume mounted at `/data`, the UI port published on the host's loopback (`127.0.0.1:8765:8765`) by default, `init: true`, a restart policy, the healthcheck, optional `env_file`/`environment` credential entries and an optional token-file secret; its build context defaults to the repository's Git URL and is overridable by one environment variable; nothing Docker-Desktop-specific (R3, R4, R5, R6)."
  - file: core/config_ui/__init__.py
    description: "Container-mode settings, each opt-in and absent by default: a one-time value-free warning whenever the bind is non-loopback; `--no-browser` (no opener call); `--token-file PATH` (the access token read from a file, validated, refused when invalid); `--start-runtime` (start the supervised runtime once at UI start when a completed profile is present, through the existing Start path); the `--health` command (state computed per A6 and R6 from the instance record, the session-free `/instance` probe, the supervision record and the status record; one value-free line; exit 0/1); the supervision record `<base>.supervisor.json` the UI publishes for it beside the profile, which joins the startup collision set (R6); and the stale-record rule for a recorded pid whose live process started after the record was published. `/instance` keeps its phase-9 contract unchanged (R1, R2, R6)."
  - file: .github/workflows/ci.yml
    description: "Add one job `container-trial` on ubuntu-latest that installs the project with its test extra and runs R7's trial invocation from the checkout (it builds the image there), pushing nothing; the existing matrix job is unchanged and runs the ordinary checks of `tests/test_container.py` with the trial nodes skipped by name (R7, AC23)."
  - file: tests/test_container.py
    description: "New: the ordinary checks of R7's test policy (no socket, no process, no Docker daemon): the container-mode settings, the warning, the token file, the start-at-launch setting, the supervision record and its collision refusal, the health states, the stale-record rule, the static contracts of Dockerfile/.dockerignore/compose.yaml/ci.yml and the trial file's own shape; plus exactly five trial nodes `test_trial_container_*`, each gated by `required(CONTAINER_TRIAL_VARIABLE)` on `TWITCH_IA_COMPAGNON_CONTAINER_TRIAL`, which build, run and probe a real container."
  - file: tests/conftest.py
    description: "Register `test_container.py` in `_TRIAL_GATES` (gate `required`, each of its five trial nodes mapped to `CONTAINER_TRIAL_VARIABLE`), so `_ac33_isolation` exempts a container trial node only when an operator opted it in; nothing else changes (R7)."
  - file: tests/test_ac33_isolation.py
    description: "Add `test_container.py` to `TRIAL_FILES` and raise the default-run trial-node count of `test_no_trial_node_is_exempt_without_its_opt_in` from 20 to 25 (R7)."
  - file: docs/self-hosting.md
    description: "New normative guide for someone who has not read the repository: quick start in at most three commands, the compose file, the `/data` volume and every file in it, environment variables and the credential file, the token, the four profiles, backup, restore and upgrade, the healthcheck states, the measured image size and the honest limits of container mode (R8)."
  - file: docs/self-hosting.fr.md
    description: "New: French translation of the self-hosting guide, same sections, commands and identifiers (R8)."
  - file: docs/README.md
    description: "Link the self-hosting guide pair from the documentation index and record that phase 10 adds the image, the compose file and the guide (R8)."
  - file: docs/installation.md
    description: "One paragraph pointing a server install to the self-hosting guide (R8)."
  - file: docs/installation.fr.md
    description: "The same paragraph in French (R8)."
  - file: docs/campaigns/phase10/trial-record.md
    description: "New: the self-hosted-instance trial record template and, after the trial, its filled record (who, host, Docker and Compose versions, the exact commands typed, timestamps, measured image size, healthcheck states observed, first run, restart over the restored volume, outcome, limits) (R8)."
  - file: tests/test_docs_bilingual.py
    description: "Add `self-hosting` to the declared guide pairs (GUIDES)."
---

# Phase 10 — distributing it: the image, the profiles it carries, the self-hosting guide

READ: /home/chpo/.hermes/cache/scratch/adversarial-spec-write-u11ns4o1/brief.md

## Problem

The product's deployment decision is « le PC du streamer **ou** un serveur distant ». Phase 9 made it
reachable on the streamer's own machine through a wizard and a console served by `core/config_ui`, and
it shipped exactly four ready-made profiles (`chat_qa`, `channel_points`, `spoken_keyword`, `silent`)
as package data. Nothing makes the second half true: someone who wants the companion on a VPS or a NAS
must read the repository, build an environment and discover the flags. The documentation says headless
operation is possible, but nothing ships that makes a container work:

- the UI binds loopback by default; `--allow-non-loopback` exists but says nothing when used, and nothing
  makes the network exposure visible in a log;
- the access token is generated per start, so a restarted container silently changes the URL, and no
  file-supplied token exists;
- the UI always hands its URL to a browser opener, which has no meaning in a container;
- the per-user state lives under `$XDG_CONFIG_HOME/twitch-ia-compagnon/`, which no image maps to a
  documented volume;
- after a container restart over the same state, the instance record (`<base>.instance.json`) and the
  status record (`<base>.status.json`) name a pid that a *new* process of the new container may hold
  (PID namespaces reuse low pids), so the UI can judge its own predecessor "running but not answering"
  and refuse to serve, or refuse to start the runtime;
- there is no healthcheck, so Docker can only say "the process started".

This phase ships one image, one compose file and a self-hosting guide pair so that a technical person
with Docker and Compose gets a first recorded run from a compose file alone, and it proves the four
profiles inside the container. It ships no native bundle and no installer, adds no runtime dependency,
no second image and no registry publication, and changes no default behaviour of the product: every new
behaviour is a setting that is off unless given, except the supervision record (R6), a new sibling file
that exists only while a UI runs and changes no existing behaviour except one: a configuration that
names its path for another file (status, overlay, feed) is now refused at startup like any other collision.

### Binding decisions carried from the brief (operator, 2026-10-25)

D1 the self-hoster is the audience and the criterion is measured in commands (no checkout, no manual
build); D2 one image from the project's own distribution, pinned base, non-root, no extra runtime
dependency, measured size; D3 secrets supplied, never baked, `${...}` stays the only way a value enters a
configuration; D4 non-loopback exposure is explicit, warned, token-protected, the token printed once,
never a credential value; D5 one documented state volume, first run on empty and no surprise on
restore; D6 a truthful healthcheck; D7 the four profiles present in the image and proven inside it; D8 a
guide pair for someone who has not read the repository.

### Assumptions (documented; the brief is silent on them)

- A1 — **"No checkout, no manual build" without a registry.** Publishing is out of scope, so the image
  cannot be pulled. The compose file's build context therefore defaults to the repository's Git URL at
  a named ref (`https://github.com/chpomob/twitch-ia-compagnon.git#main`), which Compose builds on the
  first `docker compose up` without a local clone; one environment variable
  (`TWITCH_IA_COMPAGNON_BUILD_CONTEXT`) overrides it, which CI and the trial use to build from a checkout.
  The guide says that the first `up` builds the image (and how long it took in the trial), that the host
  needs `git` for Compose's Git-context build, and that a later phase may replace the build with a
  published image. If the repository is not publicly readable, the guide's quick start requires access
  to it and says so.
- A2 — **Container mode is a command line, not a mode flag.** The image's default command starts
  `twitch-ia-compagnon-config-ui` with the per-user default location (no `--config`, phase 9 A2),
  `--host 0.0.0.0 --allow-non-loopback --no-browser --start-runtime`. Every option exists for every
  install; none changes anything unless given.
- A3 — **The volume is `/data`.** The image sets `XDG_CONFIG_HOME=/data` and `HOME=/data`, so the phase-9
  per-user directory, the default profile and the credential directory are all
  `/data/twitch-ia-compagnon/`, and every sibling file (overlay, status record, feed, wizard state,
  instance record, supervision record) lands beside the profile there. `/data` exists in the image, owned by uid/gid
  `10001:10001` with mode 0700, so a named volume inherits that ownership; a bind mount must be owned by
  uid 10001 and not group- or other-writable, or phase 9's credential refusal applies (the guide says so).
- A4 — **Published on the host's loopback by default.** The compose file publishes
  `127.0.0.1:8765:8765`. The container binds `0.0.0.0` so the published port reaches it; exposing the UI
  beyond the host is a documented edit of the compose file (the published address and one
  `--allowed-host NAME` per accepted host name), never a default.
- A5 — **The token in container mode.** Without `--token-file` the token is generated per start, as
  today, and the access URL is printed once to the container log at each start. With `--token-file PATH`
  (e.g. a Compose secret mounted under `/run/secrets/`) the token is that file's content, stable across
  restarts. A valid token is 43 to 128 characters from the URL-safe base64 alphabet (`A–Z a–z 0–9 - _`),
  i.e. at least the 256 bits of today's generated token; surrounding whitespace is stripped. Whatever its
  source, the token is checked exactly as phase 9 checks it, by the same two route classes (R1); no
  setting disables the token.
- A6 — **"Ready" means the runtime is serving.** Docker knows only healthy/unhealthy, so the health
  command computes one of five states from what the product writes (R6): `ready` and `degraded` (the UI
  answers and the runtime this UI launched reports through the status record), `starting` (launched,
  live, not yet reporting, within the Apply window), `idle` (no runtime because the wizard is unfinished,
  it was never launched since this UI start, or the operator stopped it), and `unhealthy` (the UI does
  not answer, or a runtime this UI launched and the operator did not stop is no longer live or never
  reported). A state it cannot compute from those records is not one of its states.
- A7 — **The spoken-keyword profile in a container.** A container has no microphone; the profile passes
  its check and dry run there (fake edges, as in phase 9) but cannot hear a streamer. The guide lists this
  under the limits; the profile is not changed.

## Requirements

- R1: **Container-mode settings that make headless operation safe, each opt-in.** The configuration UI
  gains: (a) a warning written once to standard error at start whenever the bind host is not loopback
  (wildcard included), naming the bound host and port and stating that the UI is reachable from the
  network and protected only by the access token, holding no token, session or credential value; (b)
  `--no-browser`, with which the browser opener is never called while the access URL is still printed
  once; (c) `--token-file PATH`, with which the access token is read from the file under A5's rules, and
  an unreadable, empty or invalid file refuses to start (exit 2, nothing served, a diagnostic naming the
  path and the rule broken, never the content); (d) `--start-runtime`, with which the UI, once listening,
  starts the supervised runtime once through the same Start path the console uses (same refusals) when
  the landing would be the console or the editor (a profile present and no unfinished wizard), and
  otherwise starts nothing and logs a value-free reason. Without these options every existing behaviour is
  unchanged. The route classes stay exactly phase 9's, whatever the options: (i) `GET /instance` is the
  one session-free route — Host check, then the access token in its query, no session read or created,
  answering only the instance marker and the product version; (ii) `GET /?token=…` (the access URL)
  exchanges the token for a session and answers only a redirect carrying the session cookie; (iii) every
  other route of the route table requires that session (and, for state-changing routes, the CSRF and
  Origin checks). No option moves a route between classes or serves a class-(iii) route without a session.

- R2: **One state volume; empty means first run, restored means no surprise.** In the image, every file
  the UI and the four profiles' runtime write (profile, overlay, status record, feed, wizard state,
  credential file, instance record, supervision record) is under `/data/twitch-ia-compagnon/` (A3) and nothing the product
  writes is elsewhere on the container's filesystem except temporary files. A container started over an
  empty volume lands on the wizard. A container started over a volume restored from a previous container
  (stopped, killed or crashed) lands where the previous one would have (console for a completed wizard,
  wizard for an unfinished one), keeps the stored credentials, and is never refused or blocked by records
  its predecessor left: a recorded pid whose live process started after the record was published is not
  the process that published it, and the record is judged as if that pid were not live. Where a process's
  start time cannot be read, the existing judgement is unchanged; the record formats are unchanged.

- R3: **One image, built from the project's own distribution.** The image is built from `Dockerfile` in
  two stages: the first builds the project's wheel from the repository, the second installs only that
  wheel on a Python base image pinned by tag and `sha256` digest, with a Python version within the
  declared support (3.10–3.13). Its installed distributions are exactly the base image's own, the project,
  and the requirement closure of the two declared dependencies (aiohttp and PyYAML); `pyproject.toml`'s
  `dependencies` are unchanged. The runtime stage contains no repository checkout (no `.git`, no `tests/`)
  and runs as numeric user `10001`, never root. Its size is measured by `docker image inspect` and stated
  in the guide and the trial record.

- R4: **One compose file that gives a first run in commands, on plain Docker.** `compose.yaml` declares
  one service built per A1 and A4, one named volume at `/data`, `init: true`, a restart policy and the
  healthcheck (R6). It uses only features of Docker Engine with the Compose v2 plugin on Linux (no
  `host.docker.internal`, no Docker Desktop extension, no profile or override file required). With it, a
  first recorded run is reached with no repository checkout and no manual `docker build`: obtain the
  compose file, `docker compose up -d`, read the access URL from `docker compose logs`, then the wizard in
  a browser.

- R5: **Secrets are supplied, never baked, never logged.** Credentials and model keys reach the product
  only as environment variables given to the container (`environment`/`env_file`) or as the phase-9
  credential file in the volume; the profile keeps `${NAME}` references only, and the existing precedence
  (a stored credential overrides an inherited variable of the same name) is unchanged and documented. No
  image layer, image environment or image label holds a credential, a key, a token, an operator profile
  (a rendered or operator-supplied `profile.yaml` or overlay), a credential file or an `.env` file; the
  four bundled example profiles that R7 requires are exempt, as they hold `${NAME}` references only. The
  container log (UI and supervised runtime, stdout and stderr) holds the access URL (and so the token)
  exactly once per UI start and never a credential value; the health log holds neither the access URL,
  the token nor a credential value.

- R6: **The healthcheck tells the truth, computed from records the product writes.** The installed
  distribution offers a health command (`twitch-ia-compagnon-config-ui --health`, with the same
  `--config`/default-location resolution) that needs no tool beyond the image, opens only a loopback
  connection to the recorded port, finishes within 5 seconds, prints exactly one line and holds no token,
  session or credential. It reads these and nothing else:
  1. the **instance record** `<base>.instance.json`, phase 9's format unchanged (pid, port, marker, and on
     POSIX the access URL, whose token the probe presents);
  2. the **`/instance` probe**: one `GET /instance?token=…` to `127.0.0.1:<recorded port>` — R1's class
     (i), so it holds and creates no session — answering the marker and the version only;
  3. the **supervision record** `<base>.supervisor.json` (new, the supervisor's state made observable):
     the UI publishes it atomically (owner-only mode, under the instance record's lock) right after it
     claims its instance record, rewrites it at each supervisor transition below, and removes it when it
     releases its instance record. Its fields are `version` (1), `marker` (this UI's instance marker),
     `state` (`not_started`, `launched` or `stopped`) and, when `launched`, `pid` (the child's) and
     `launched_at` (a UTC timestamp in the status record's format). The UI writes `not_started` when it
     publishes it, `launched` each time `Supervisor.start` returns a child (console Start, Apply's restart,
     `--start-runtime`), and `stopped` when the console's Stop stops this UI's child (outcome `stopped`);
     nothing else writes it. Only `main`'s claim path publishes it: a `ConfigUI` that has not published one
     (built directly, or a launch that never claimed its instance record — a second launch, a refused
     startup, a failed publication, `--install-launcher`) writes none at any transition. Its publication
     replaces any supervision record present (this UI now holds the instance record); its removal at
     release removes it only when it carries this UI's marker, whether or not the instance record is still
     this UI's, and keeps one carrying another marker. **Where it lives:** beside the profile, in the
     profile's directory, next to the instance record (`<base>.instance.json`) and the wizard state
     (`<base>.wizard.json`) and, at their defaults, the overlay, the status record and the feed; never in
     the per-user credential directory unless that is the profile's directory (in the image both are
     `/data/twitch-ia-compagnon/`, A3). **It joins the startup collision set** (phase 9's R1, as extended by
     its B1 to the wizard state and the instance record) under the same rule: `<base>.supervisor.json` may
     not resolve (canonical form, same file) to the base profile, the overlay, the status record, the feed
     path, the wizard state file, the credential file or the instance record; it is checked after all of
     them, so a collision is reported once, as `supervision_path_collision` with the role "supervision
     record"; a collision refuses to start (exit non-zero, nothing served, nothing published) with a
     value-free diagnostic naming the supervision record's path and the file and role it collides with,
     never any file's content. The health command does not run the startup checks: a colliding
     configuration never serves, so rows 1–2 decide before row 5 is reached;
  4. the **status record** `<base>.status.json`, its format unchanged, classified by the existing reader;
  5. the **wizard state**, only through the landing decision the access URL uses (wizard or not).

  A pid is live by the existing judgement with R2's stale rule, `launched_at` standing for the
  publication time. The rows are evaluated in order; the first that matches decides the line and the exit
  status:

  | # | What the records show | Line printed | Exit |
  |---|---|---|---|
  | 1 | no instance record, or an incomplete one | `unhealthy: no instance record` | 1 |
  | 2 | the probe gets no answer (refused, silent, or past the 5-second budget) | `unhealthy: not answering` | 1 |
  | 3 | the probe is refused the token (401) | `unhealthy: token refused` | 1 |
  | 4 | the probe answers anything but 200 with the record's marker | `unhealthy: another instance` | 1 |
  | 5 | a supervision record is present but does not parse or a field is invalid | `unhealthy: supervision record unusable` | 1 |
  | 6 | no supervision record, one with another marker, or `not_started`; the landing is the wizard | `idle: wizard unfinished` | 0 |
  | 7 | as row 6, the landing is not the wizard (profile completed, never launched since this UI start) | `idle: not started` | 0 |
  | 8 | `stopped` (the operator stopped it from the console) | `idle: stopped` | 0 |
  | 9 | `launched`, and its pid is not live (the supervised process exited, the UI still serving) | `unhealthy: runtime exited` | 1 |
  | 10 | `launched`, pid live, a usable status record whose pid is that pid, every module `ready` | `ready` | 0 |
  | 11 | as row 10, at least one module `degraded` | `degraded: <the degraded module names, sorted, comma-separated>` | 0 |
  | 12 | `launched`, pid live, no usable status record of that pid, at most the Apply window (60 s) since `launched_at` | `starting` | 0 |
  | 13 | as row 12, more than the Apply window since `launched_at` | `unhealthy: runtime not ready` | 1 |

  "Answering" thus means the probe confirms the instance record's marker — not that a process exists or a
  port is open. The compose file and the image declare `twitch-ia-compagnon-config-ui --health` as the
  container's healthcheck with a start period; the healthcheck uses the session-free probe and never a
  session.

- R7: **The four profiles travel with the image and are proven inside it.** The image holds the four
  profiles byte-identical to the checkout. Inside a running container of the image, run by its own
  interpreter as user 10001, each profile rendered by the wizard's generator with fixture parameters
  passes `twitch-ia-compagnon --check-config` (exit 0) and its dry run (fake platform edges, a fake model
  transport, nothing sent anywhere) returns a run record with the profile's declared expected outcome
  (`completed` for `chat_qa`, `channel_points`, `spoken_keyword`; `not_triggered` for `silent`). The proof
  code is supplied to the container at trial time, never baked into the image.

  **Test policy.** The ordinary suite stays green with no Docker daemon; the container trial is real and
  runs where one exists. Both live in `tests/test_container.py`:
  - *Ordinary checks* run in every `python -m pytest tests/` (the existing matrix job included) under the
    AC33 isolation guard, opening no socket and starting no process: they read `Dockerfile`,
    `.dockerignore`, `compose.yaml` (parsed with PyYAML) and `.github/workflows/ci.yml`, assert the image's
    declared properties (stages, pinned base, numeric user, default command, healthcheck, ignore
    patterns, compose keys, the CI job's steps), compare the guide's and the trial record's sizes, and
    drive the UI's settings, the supervision record, the health rows and the stale rule against fakes —
    AC1–AC6, AC10, AC11, AC13's agreement, AC14, AC17's `.dockerignore` part, AC18–AC20, AC23 and the
    trial file's shape below.
  - *Trial checks* are exactly five nodes, `test_trial_container_image` (AC12, AC13's measurement, AC17's
    image part), `test_trial_container_volume` (AC7, AC8, AC9), `test_trial_container_secrets` (AC16),
    `test_trial_container_health` (AC21) and `test_trial_container_profiles` (AC22). Each begins with
    `required(CONTAINER_TRIAL_VARIABLE)`, phase 2's gate: when `TWITCH_IA_COMPAGNON_CONTAINER_TRIAL` is
    unset or empty it skips with the reason `TWITCH_IA_COMPAGNON_CONTAINER_TRIAL is unset`; once it is set,
    a missing `docker` executable, an unreachable daemon or a failed build is a **failure** naming the
    cause, never a skip. They drive `docker compose` with the build context set to the checkout, wait only
    through Docker's own bounded waits (`docker compose up --wait --wait-timeout`, `docker events --until`,
    subprocess timeouts), so the suite's no-positive-sleep check stays unchanged, and remove the
    containers and volumes they created.
  - *Invocation*: `TWITCH_IA_COMPAGNON_CONTAINER_TRIAL=1 TWITCH_IA_COMPAGNON_BUILD_CONTEXT=. python -m
    pytest tests/test_container.py -q -p no:cacheprovider -rs` from a checkout, run by the CI job
    `container-trial`, by the phase's trial and gate, and by an operator on a host with Docker; the trial
    record names the commit and the five nodes' outcomes.
  - *Never silent, never pretended*: the default run reports the five nodes as skipped with that reason
    (visible under `-rs`), and no other node of the file skips. An ordinary check asserts the file has
    exactly those five `test_trial_` nodes, each gated as above, and no `skipif`, `xfail`,
    `importorskip` or other `pytest.skip(` in the file. The AC33 guard exempts a trial node only when it
    is opted in: `tests/conftest.py` registers the file in `_TRIAL_GATES`, and `tests/test_ac33_isolation.py`
    adds it to `TRIAL_FILES` with the default-run count raised from 20 to 25. The CI job pushes nothing.

- R8: **A guide written for someone who has not read the repository, and a trial record.**
  `docs/self-hosting.md` (normative) and `docs/self-hosting.fr.md` (same sections and identifiers) cover:
  the quick start in commands (R4), the compose file line by line, the `/data` volume with every file in it
  and its role, the first run on an empty volume and the restore of a volume, the environment variables
  and the credential file with their precedence, the token (printed once per start, `--token-file`,
  exposure beyond the host with `--allowed-host`), the four profiles, backup, restore and upgrade, the
  healthcheck states, the measured image size, and the limits of container mode (no desktop session, no
  browser opener, no launcher, no microphone, no native bundle). The documentation index links the pair,
  the installation guide pair points to it, and `docs/campaigns/phase10/trial-record.md` records one
  operator trial of a self-hosted instance.

## Acceptance criteria

- AC1 (R1): Started with a wildcard host (`0.0.0.0`) and `--allow-non-loopback`, the UI writes exactly 1
  warning line to standard error before serving, naming `0.0.0.0` and the port and the word "token"; the
  line holds neither the access token nor any session or credential value. Started on `127.0.0.1` it
  writes 0 such lines.
- AC2 (R1): With `--no-browser` the injected opener is called 0 times and the access URL is printed
  exactly 1 time; without it the opener is called exactly 1 time (unchanged).
- AC3 (R1): With `--token-file` naming a file holding a valid 43-character URL-safe token plus a trailing
  newline, the printed access URL ends in `?token=` followed by exactly those 43 characters, and a request
  presenting that token obtains a session. For each of: a missing file, an empty file, a 42-character
  token, a 129-character token, and a token containing `+`, the UI exits with status 2, serve is called 0
  times, and standard error names the path but not the file's content.
- AC4 (R1): With `--start-runtime` and a profile whose wizard state is completed, the supervisor's start
  is called exactly 1 time after listening; with an unfinished wizard or no profile it is called 0 times
  and a reason line holding no value is logged; with a live process holding the status record it is
  refused exactly as the console's Start refuses it. Without `--start-runtime` it is called 0 times.
- AC5 (R1): The route classes are phase 9's under every combination of `--token-file`, `--no-browser`,
  `--start-runtime` and a non-loopback bind: for every route of class (iii) in the route table, a request
  with an accepted Host and no session receives the same refusal it receives today; `GET /instance` with
  the token and no session cookie answers 200 with exactly the keys `marker` and `version` and sets no
  cookie, and without the token answers 401; `GET /?token=…` with the token answers a redirect that sets
  the session cookie and no content.
- AC6 (R1): The existing test suite passes (5,253 tests at the brief's HEAD, plus this phase's) with only
  the adaptations R7's test policy names (`tests/conftest.py`'s `_TRIAL_GATES`,
  `tests/test_ac33_isolation.py`'s `TRIAL_FILES` and count) and `tests/test_docs_bilingual.py`'s `GUIDES`,
  demonstrating that the defaults of every existing behaviour are unchanged. Every existing assertion of
  the exact set of files in a UI directory passes unedited, for the reasons the section "Exact-directory
  assertions" gives per test; none is weakened, filtered or made to ignore a directory.
- AC7 (R2): In a running container of the image, after the wizard's screen 4 and 5 have been passed, a
  listing of files modified after the container's start, excluding `/proc`, `/sys`, `/dev`, `/run` and
  `/tmp`, holds only paths under `/data/twitch-ia-compagnon/`, and that directory holds the profile, its
  overlay path's file if written, the status record, the feed, the wizard state, the credential file, the
  instance record and the supervision record.
- AC8 (R2): A container of the image started over an empty volume answers the access URL with a redirect
  to `/wizard`.
- AC9 (R2): Over a volume whose wizard is completed, after `docker kill` of the container (no clean exit)
  and `docker compose up -d` again, the new UI serves within its start period (health state not
  `unhealthy`), the access URL lands on `/console`, the credential file is byte-identical to before, and
  with `--start-runtime` the runtime is running (health `ready` or `degraded`).
- AC10 (R2): Socket-free: an instance record and a status record each naming a pid that is live but whose
  process start time is later than the record's publication time are judged not live — the instance
  record is removed as stale and this launch serves; Start is not refused because of the status record.
  The same records with a pid whose process started before publication are judged as today (the
  existing instance-record and foreign-pid tests pass unchanged), and with an unreadable start time the
  judgement is unchanged.
- **Amendment A3 (2026-10-26) — a `scratch` final stage is allowed, and required where it removes an
  inherited environment.** AC11 as written demanded exactly two `FROM` lines, and the image could not
  meet AC17's own prohibition (no build environment in the delivered image) while inheriting
  `GPG_KEY` from the Python base: `docker image inspect --format '{{json .Config.Env}}'` shows it, and a
  base image's `ENV` cannot be unset by a later instruction. The delivered filesystem may therefore be
  re-based onto `FROM scratch` after the wheel has been installed, so that nothing of the base's
  environment survives. What AC11 protected still holds and is what the checks assert: the stage that
  installs the project names a Python base pinned by tag **and** `sha256`, the final image runs
  `id -u` → `10001` (AC12), `python -m pip list` shows exactly the declared closure (AC12), and its
  environment carries only the variables the specification names (AC17). A third stage that carries
  anything else — a repository directory, a build tool, a key — remains a defect.
- AC11 (R3): `Dockerfile` has the stages A3 allows, and its final stage names no image where A3 applies; the final one names an image as
  `python:<3.10–3.13 version>-<variant>@sha256:<64 hex characters>`; the final stage installs a wheel built
  by the first and copies no repository directory; the final stage's `USER` is `10001` (numeric).
- AC12 (R3): Inside a container of the image, `id -u` prints `10001`; `python -m pip list --format=json`
  minus the base image's own distributions is exactly `twitch-ia-compagnon` plus the requirement closure
  of `aiohttp` and `PyYAML` (computed from the installed metadata); `/.git`, any `tests/` directory of the
  project and any `pyproject.toml` are absent; `pyproject.toml`'s `dependencies` in the checkout are
  exactly the two entries of the brief's HEAD.
- AC13 (R3, R8): The image size reported by `docker image inspect --format '{{.Size}}'` in the trial is
  recorded in the trial record in bytes, and the guide states it in MB rounded to 1 decimal; the two agree
  within 5 %.
- AC14 (R4): `compose.yaml` parsed with PyYAML has exactly 1 service and 1 named volume; the service mounts
  that volume at `/data`, publishes `127.0.0.1:8765:8765`, sets `init: true`, a `restart` policy and a
  `healthcheck` whose test runs the health command; its build context is
  `${TWITCH_IA_COMPAGNON_BUILD_CONTEXT:-https://github.com/chpomob/twitch-ia-compagnon.git#main}`; the file
  contains no `host.docker.internal`, no `extensions`, no `x-docker-desktop` key and requires no other
  compose file.
- AC15 (R4, R8): The trial record shows a first recorded run (wizard screen 5 passed) on a Linux host with
  Docker Engine and the Compose v2 plugin, from a directory holding only `compose.yaml` (and optionally an
  env file), reached with at most 3 typed commands — one to obtain the compose file, `docker compose up -d`,
  one to read the access URL — none of which is `git clone` or `docker build`; the guide's quick start
  lists the same at most 3 commands.
- AC16 (R5): With a container started with sentinel credential values (one distinct 24-character sentinel
  per credential the four profiles name) in its environment, and after a wizard run and a dry run: the
  sentinels appear 0 times in `docker compose logs` and in the health log of `docker inspect`; the access
  token appears exactly 1 time in `docker compose logs` per UI start (inside the access URL) and the
  access URL and the token each appear 0 times in the health log.
- AC17 (R5): `docker image inspect` of the image shows no environment variable or label whose name is a
  credential name of the four profiles or matches `*_KEY`, `*_TOKEN`, `*_SECRET` or `*PASSWORD*`; the image
  filesystem holds 0 files named `profile.yaml`, `*.credentials.json`, `*.local.yaml`, `.env*` or
  `*.instance.json`, and `/data` is empty, while the 4 bundled profiles of AC22 (outside `/data`) are
  present (exempt per R5); `.dockerignore` excludes `.git`, `*.local.yaml`,
  `*.status.json`, `*.credentials.json`, `.env*` and `docs/runs/`.
- AC18 (R5): Socket-free: with a credential both inherited from the environment and stored in the
  credential file under the same name, the supervised runtime's environment holds the stored value
  (unchanged precedence), and the guide states this precedence in both languages.
- AC19 (R6): Socket-free, against fake records, a fake probe, a fake liveness judgement and an injected
  clock, the health command prints exactly 1 line, equal to R6's "Line printed", and exits with that row's
  status, for each of R6's 13 rows (row 2 once for a refused connection and once for a silent port); a
  case matching two rows takes the earlier. The probe it makes carries the record's token and no session
  cookie. Socket-free too: the UI writes the supervision record `not_started` after claiming its instance
  record, `launched` with the child's pid on console Start, Apply's restart and `--start-runtime`,
  `stopped` on the console's Stop of its child (and not on an Apply), and removes it on release — also
  when its instance record was replaced by another during serving, while a supervision record carrying
  another marker is left byte-identical; a `ConfigUI` built without `main`'s claim writes none on Start or
  Stop. The line never holds the token, a session or a credential. Socket-free too: with the status
  path, in a second case the overlay path (both as phase 9's AC1 configures them) and in a third the feed
  path (through its environment variable) configured to `<base>.supervisor.json`, each with a fixture content string written to that file,
  `startup_checks` returns exactly 1 problem, `supervision_path_collision`, naming the supervision record's
  path and the colliding file's role and path, with 0 occurrences of the fixture content, and
  `main([], serve=<fake>)` exits non-zero with `serve` called 0 times and the file byte-identical; with no
  collision `startup_checks` returns 0 problems for the default location (phase 9's AC1 unchanged).
- AC20 (R6): The health command's probe opens connections only to a loopback address and completes in at
  most 5 seconds of the injected clock against a probe that never answers, then reports `unhealthy`.
- AC21 (R6): In the container trial, `docker inspect` reports `healthy` once the UI serves; after the
  supervised runtime's process is killed with SIGKILL inside the container (not stopped from the console)
  the state becomes `unhealthy` within 3 health intervals and the health log's last line is
  `unhealthy: runtime exited`; after killing the UI process the container exits or is reported
  `unhealthy`, never `healthy`.
- AC22 (R7): Inside a container of the image, the 4 profile files under the installed
  `core/config_ui/profiles/` are byte-identical to the checkout's; for each of the 4 profiles, rendered
  with fixture parameters, `twitch-ia-compagnon --check-config` exits 0 and the dry run returns a record
  whose outcome is `completed` for `chat_qa`, `channel_points` and `spoken_keyword` and `not_triggered` for
  `silent`, with origin `dry_run`, all run as uid 10001 with no network transport opened.
- AC23 (R7): With `TWITCH_IA_COMPAGNON_CONTAINER_TRIAL` unset, a run of `tests/test_container.py` reports
  exactly 5 skipped nodes, each a `test_trial_container_*` node with the reason
  `TWITCH_IA_COMPAGNON_CONTAINER_TRIAL is unset`, and every other node passes; with it set and the `docker`
  executable made unfindable, each trial node fails (not skips) naming `docker`. The file-shape check of
  R7's test policy passes, `_TRIAL_GATES` and `TRIAL_FILES` name the file, and the default-run count is 25.
  The CI workflow gains exactly 1 job, `container-trial`, whose test step runs R7's invocation (the
  variable set to `1`, the build context `.`); the existing matrix job's steps are unchanged; no step of
  the workflow runs `docker push`, `docker login`, `docker tag` to a registry name, or creates a Git tag
  or release.
- AC24 (R8): `docs/self-hosting.md` and `docs/self-hosting.fr.md` exist with the same number of `##`
  sections and the same code identifiers (the existing bilingual checks pass with `self-hosting` declared);
  the English guide names every file of AC7 by its name under `/data/twitch-ia-compagnon/`, every option of
  R1, the health states of A6, uid 10001, and the limits listed in R8.
- AC25 (R8): `docs/README.md` links both guide files; `docs/installation.md` and `docs/installation.fr.md`
  each link the guide of their language; `docs/campaigns/phase10/trial-record.md` holds every field listed
  for it in this spec's target list, with the outcome filled after the trial.

## Caller enumeration

The public surfaces that change are the configuration UI's command line (four new options, each
defaulted off) and its settings record (new fields with defaults). Search method: `grep -rn` over the
repository for `UISettings(`, `from_argv`, `config_ui.main(` and `InstanceSeams(`, plus the two console
scripts in `pyproject.toml`; dynamic construction (e.g. `**kwargs` built elsewhere) is not detected.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/config_ui/__init__.py | `UISettings.from_argv`, `main`, `startup_checks` | Parse and apply the four options; defaults leave behaviour unchanged |
| tests/test_config_ui.py | `UISettings(...)` / `config_ui.main(...)` (38 sites) | None: new fields and options are defaulted |
| tests/test_first_run.py | `UISettings(...)` / `main` / `InstanceSeams(...)` (51 sites) | None: liveness stays injectable; the stale rule does not change injected-liveness results |
| tests/test_console.py | `UISettings(...)` (8 sites) | None |
| tests/test_input_ownership.py, tests/test_main.py, tests/test_status_record.py | one site each | None |
| pyproject.toml | `twitch-ia-compagnon-config-ui` script | Unchanged entry point; `--health` is an option of it |

`core.main` (`main`, `check_config`, `dry_run`) is called, not changed.

### Exact-directory assertions (the supervision record's new sibling file)

The supervision record is one more file in a claimed UI's directory between its claim and its release.
Search method: `grep -n` for `iterdir()`, `rglob("*")` and `_snapshot(` over `tests/`, each hit read for
whether it lists a directory a `main` claim path can publish into, while or after serving. The rules of
R6 item 3 (published only by `main`'s claim path, removed at release when it carries this UI's marker)
leave every hit unaffected; none is adapted, so no test file joins the targets and no allowlist entry is
needed. Had an adaptation been needed, adding the supervision record's name as a constant to an expected
set would be allowlistable on the same terms as R7's adaptations; filtering it out, or ceasing to list
the directory, would not.

| Test (file:line of the assertion) | Verdict and reason |
|---|---|
| tests/test_first_run.py:2900 `test_ac31_a_stale_record_is_replaced_and_this_launch_serves` (`== []` after `main`) | Unaffected: asserted after a clean exit, whose release removes the record; the assertion now also proves that removal |
| tests/test_first_run.py:2957 `test_ac31_a_clean_exit_keeps_a_record_that_is_no_longer_its_own` (`== [record.name]`) | Unaffected: the supervision record still carries this UI's marker, so release removes it although the instance record is foreign |
| tests/test_first_run.py:3107 `test_ac31_a_failed_publication_leaves_no_record_and_serves_nothing` (`== []`) | Unaffected: the instance record is never claimed, so nothing is published |
| tests/test_first_run.py:3168 `test_ac31_two_launches_racing_serve_once_and_the_loser_opens_the_winner` (`== []`) | Unaffected: the loser never claims; the winner's release removes its own, asserted after the winner's thread is joined |
| tests/test_first_run.py:3202 `test_ac31_a_removal_holds_the_name_against_a_publication` | Unaffected: drives `_publish_instance`/`_remove_instance_if` directly; no UI, no claim |
| tests/test_first_run.py:2629/2669 `test_ac4_instance_route_guards_and_answer` (`rglob` before/after) | Unaffected: a `ConfigUI` built directly (no claim) with 0 supervisor calls |
| tests/test_first_run.py:3335–3377 the AC27 launcher tests | Unaffected: `--install-launcher` exits before any claim |
| tests/test_first_run.py:1467, 1559, 1611, 1640, 1681, 1692, 1726, 1781, 1862 | Unaffected: `wizard.CredentialStore`/`WizardState` unit tests; no UI |
| tests/test_config_ui.py:3446 `test_ac28_the_write_targets_only_the_managed_overlay` | Unaffected: drives `_write_overlay` directly; no UI start |
| tests/test_config_ui.py `_snapshot(` sites (431, 456, 555, 643/648, 682, 5488, 5514) | Unaffected: either a `ConfigUI` built directly (no claim) or a `main` refused before its claim (`serve` replaced by `_fail`) |
| tests/test_console.py:2053/2117 `test_ac4_every_new_route_is_refused_by_the_existing_guards_with_no_effect` | Unaffected: a `ConfigUI` built directly with 0 supervisor calls |
