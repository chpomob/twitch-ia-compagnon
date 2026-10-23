---
spec: "phase9-first-run"
version: "1.0"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: true
---

# Implementation Plan

Authority: `spec.md` (R1–R8, AC1–AC32, assumptions A1–A7 with A3b, binding decisions D1–D8 and
Amendment A1), the brief `docs/campaigns/phase9/brief.md`. Pre-phase tree for evidence: `cb0f5b1`
(the `*.example` baseline of AC10) — the phase branch starts at `b7ec50d`. Revised against the review
findings in `findings.json` (`findings-input: true`): F-P1 → P6/P12/P13 (landing split), F-P2 →
dependency edges on P5, P8, P9, P10, P12, P13 and the shared-file ordering table, F-P3 → P12 (keyed
in-memory credential fingerprints), F-P4 → P17 (Files), F-P5 → P1 (scan fixtures).

## Design decisions taken by this plan (flagged, each justified by the spec)

- **DD1 — the core platform/vendor scans conflict with the spec's targets (out-of-list blocker, flagged).**
  Three existing guards scan every file under `core/`:
  `tests/test_agentic_loop.py::test_ac31_the_core_and_the_neutral_modules_name_no_platform` (substring
  `twitch`, case-sensitive), `tests/test_youtube.py::test_the_broad_core_scan_names_no_platform_or_vendor`
  (words `twitch|kick|youtube|openai|anthropic|gemini`, case-insensitive) and
  `tests/test_youtube.py::test_ac39_core_names_no_platform` (words `kick|youtube`). The spec places
  platform-named content under `core/config_ui/`: the profiles must enable the `twitch` module and
  reference its credentials (R3), screen 2 is Twitch (R5) and its sentences name it (R2), and the A1/A2
  per-user directory is `twitch-ia-compagnon` (which `\btwitch\b` matches). The spec's allowlist does not
  name these guards. This plan keeps every platform-named byte in exactly three places:
  `core/config_ui/profiles/` (profile data, including the Twitch validation/reward endpoints and scopes
  as data), `core/config_ui/strings/` (sentences), and one new constant module
  `core/config_ui/locations.py` (the product directory/distribution name and the A1/A2 directory
  functions). `wizard.py`, `console.py`, `launcher.py`, `__init__.py`, `core/main.py` and
  `core/overlay.py` stay platform- and vendor-free and remain scanned (the wizard reads the platform's
  endpoints from the chosen profile's data). P1 adapts the first two guards to exempt exactly those
  three paths, asserting the exemption set is exactly that and that every other `core/` file is still
  scanned; the `kick|youtube` guard (AC39) is **not** adapted and keeps covering the exempt paths too
  (nothing this phase writes names those platforms). The step's commit message and the full-branch
  review must flag this for operator ratification at the phase gate; no assertion is weakened for any
  file that exists before this phase.
- **DD2 — the model endpoint is described as a "chat-completions" endpoint**, never by vendor name, in
  code and in strings, so the vendor scan needs no exemption beyond DD1's data files.
- **DD3 — language persistence lives in the wizard state file (A7), the only non-secret per-profile
  file this phase adds beside the profile** (R1 allows no fourth). For a profile with no state file
  (hand-written or pre-phase), a console language choice writes a state file holding the language,
  `completed: true` and an empty ownership record (the wizard never ran and owns nothing) — so A7's
  "no state file and a profile present → console/editor" outcome is unchanged and the wizard is never
  offered.
- **DD4 — one string-table loader module `core/config_ui/i18n.py`** (not a spec target: the spec puts
  the data in `strings/` but names no loader; both `wizard.py` and `console.py` need it, so it cannot
  live in either without an inverted dependency). It discovers `strings/*.json` and any language file
  beside them, offers only complete key sets (R2, AC6).
- **DD5 — profiles are JSON data, one file per profile** (`chat_qa.json`, `channel_points.json`,
  `spoken_keyword.json`, `silent.json`): each holds its parameter slots, credential references, required
  scopes, the platform validation/reward endpoints, the dry-run fake event and a profile template; the
  generator in `wizard.py` substitutes the slots and dumps YAML. JSON keeps substitution structural (no
  text templating of YAML) and the `profiles/*` package-data glob ships them (AC26).
- **DD6 — the dry run is a core entry over the generated profile with the platform edges replaced.**
  `core/main.py` gains `dry_run(config_path, event, *, environ, clock, model_transport, timeout)`: it loads
  and validates the profile exactly as `check_config` does, assembles the runtime, activates every
  enabled module with its network seams (`_session_factory`) replaced by a socket-free inert session
  except the model transport, binds a recording provider in place of each provider the platform input
  module declares (so 0 messages reach a channel), evaluates the fake event through the same
  `TriggerEngine` (`core/runtime.py`, the engine the input modules call) under the profile's resolved
  policy, publishes it with the input's event shape, and returns a `DryRunRecord(run_id, outcome,
  origin="dry_run", event=…)` taken from the admission scheduler's record. The real model is used in
  production (what screen 3 validated); every test injects a fake. The mechanism names no platform: the
  platform module is identified from the manifests (the module owning the profile's input pair).
- **DD7 — the editor's base page gains exactly one link to the console**, and only where AC2 allows it
  (one added link on `/`, no other editor markup change). If an existing `tests/test_config_ui.py`
  assertion pins that page's markup exactly, the link is dropped (0 added links also satisfies AC2) and
  the console stays reachable at `/console`; the step records which case held.
- **DD8 — new routes are exactly** `/wizard` (GET, POST; the screen and the read-only YAML view are
  parameters of it), `/language` (POST), `/console` (GET), `/feed` (GET, token-gated poll), `/start`,
  `/stop`, `/revoke` (POST) and `/instance` (GET, token-authenticated, sessionless). No other route is
  added (AC16, AC23).
- **DD9 — the feed is a JSON-lines file `<base>.feed.jsonl` beside the status record**, rewritten
  atomically from an in-memory ring of 200 entries by a background writer; each entry carries a
  monotonically increasing sequence used as the console's poll cursor. The bus handler only enqueues
  (never raises, never awaits I/O); a write failure is reported once, value-free, through the runtime's
  diagnostic reporter (not swallowed silently).

## Steps

### P1: Confine platform-named bytes in core and adapt the two core scans (DD1, DD2; prerequisite of R2, R3, R5, A1, A2; flagged out-of-list)
- **Files:** [`core/config_ui/locations.py`, `tests/test_agentic_loop.py`, `tests/test_youtube.py`, `tests/test_first_run.py`]
- **Description:** Create `core/config_ui/locations.py` holding the product/distribution name constant
  and the per-platform directory functions A1 and A2 need: `default_profile_path(platform, environ)`
  (`$XDG_CONFIG_HOME/<product>/profile.yaml` falling back to `~/.config/<product>/`; `%APPDATA%\<product>\`
  on Windows; `~/Library/Application Support/<product>/` on macOS), `credential_directory(platform,
  environ)` (A1: the same per-user directory on Linux/macOS, `%LOCALAPPDATA%\<product>\` on Windows),
  `credential_file_name(profile_path)` (`<profile-file-name>.credentials.json`) and
  `product_version()` (installed distribution's version, `importlib.metadata`, with a value-free
  fallback when not installed). The platform and environment are parameters so every test forces them.
  Adapt `test_ac31_the_core_and_the_neutral_modules_name_no_platform` (the `core` parameter) and
  `test_the_broad_core_scan_names_no_platform_or_vendor` to skip exactly
  `{core/config_ui/locations.py, core/config_ui/profiles/, core/config_ui/strings/}` — a module-level
  frozenset, asserted to be exactly those three paths, all under `core/config_ui/` — and to assert that
  every other file under `core/` is still read; `test_ac39_core_names_no_platform` is not touched.
  Flag the adaptation in the commit message as an out-of-list edit for operator ratification.
- **Dependencies:** [] (none — can run first)
- **Tests:** In `tests/test_first_run.py`: AC1's path half — `default_profile_path("linux",
  {"XDG_CONFIG_HOME": tmp})` = `<tmp>/twitch-ia-compagnon/profile.yaml`, with `XDG_CONFIG_HOME` unset and
  `HOME=<tmp>` = `<tmp>/.config/twitch-ia-compagnon/profile.yaml`, Windows/macOS forms; credential
  directory per platform (Windows `LOCALAPPDATA`), credential file name for `a.yaml` vs `a.yml` distinct
  (AC11 naming). Each adapted scan is proved by calling its scan helper on a tmp tree, with fixtures
  matched to each scan's own contract: a fixture core file outside the exemption containing the
  lowercase word `twitch` fails **both** scans (it satisfies the first scan's case-sensitive substring
  and the second's case-insensitive word match); a second fixture containing only `Twitch` fails the
  broad case-insensitive scan (its case-insensitivity is kept) and is **not** claimed to fail the
  case-sensitive first scan; the same two fixtures placed inside an exempt path fail neither adapted
  scan. Both scans pass on the repository.
- **Risks:** The exemption is a weakening of a guard if it grows: pin the set exactly and keep it
  limited to files this phase creates. `importlib.metadata` may not find the distribution in a bare
  checkout — `/instance` and the version comparison (P8) must use the same function so both sides agree;
  the fallback string must be stable. Writing the product name anywhere else in core (docstrings
  included) fails the scans: reviewers grep for it.

### P2: Feed path, bounded redacted feed writer and its collision refusal (R6; AC18, AC19)
- **Files:** [`core/overlay.py`, `core/main.py`, `tests/test_console.py`]
- **Description:** In `core/overlay.py` add `FEED_FILE_VARIABLE` (named in the existing
  `STATUS_FILE_VARIABLE` style), `default_feed_path(base)` (`<base>.feed.jsonl`, DD9) and a
  `feed_path_collision(feed, base, overlay, status)` on file identity (`same_file`). In `core/main.py`'s
  `run()`, when the variable names a path: refuse at startup, before the configuration is read, with a
  diagnostic naming which file it collides with (base, overlay or status), exactly as the status path
  does; otherwise create a `_FeedPublisher` that subscribes (last order) to the supervision facts —
  platform event arrived (kind, platform, author display name, excerpt ≤ 80 chars),
  `input.trigger.accepted/rejected`, `brain.admission.accepted/rejected`, run started, `brain.run.completed`
  (status + reason), refused actions, `module.ready/degraded/stopped` (with cause), runtime started and
  stopping. Each entry is redacted against the runtime's `secrets` (the loader's known credential set)
  before serialisation, truncated so the serialised entry is ≤ 1 024 bytes, appended to a 200-entry
  ring with a sequence number; a background writer rewrites the file atomically (temp + replace,
  owner-only on POSIX). The handler never raises and never awaits I/O; a write failure is reported once,
  value-free. Without the variable, nothing is subscribed and `run()` is byte-for-byte the same path.
- **Dependencies:** []
- **Tests:** In `tests/test_console.py` (against a fake-edged runtime/bus, no socket): AC18 — 250
  facts → exactly the latest 200 in publication order; a 5 000-char chat message → entry ≤ 1 024 bytes,
  excerpt ≤ 80 chars; a `secrets` value in the text is redacted; a raising writer leaves the publication
  completing and every handler invoked (and the failure reported once). AC19 — without the variable, 0
  feed files and the same bus history/status records (the existing runtime suites unmodified); a feed
  path resolving to base, overlay or status refuses at startup naming which.
- **Risks:** The handler must not reorder or slow the bus (await nothing; a sync handler); the
  coordinator's drain tie on `ManualClock` (memory) can make shutdown assertions flaky — assert the
  outcome, not status 0. Redaction must run on the entry before truncation so a secret split by
  truncation cannot leak a prefix: redact, then truncate on character boundaries, then re-check size.
  Event names are taken from the existing constants, not retyped.

### P3: The string table and its loader (R2; AC5, AC6 discovery half)
- **Files:** [`core/config_ui/i18n.py`, `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json`, `tests/test_first_run.py`]
- **Description:** `i18n.py` (DD4): `StringTable.load(directory)` reads every `*.json` in the strings
  directory (injectable for tests), uses `en.json` as the reference key set, offers a language only when
  its key set equals the reference, every value is a non-empty string and every key's `{placeholder}`
  set equals the reference's; `sentence(language, key, **values)` formats with exactly the declared
  placeholders; `unknown_reason(language, token)` returns the generic sentence naming the token.
  `languages()` lists offered codes with their display names (a key of each file). The English and
  French files hold every sentence of the wizard, console, causes/next actions, the four profiles' names
  and descriptions and the raw-detail disclosure label; keys are added as P10–P15 need them (each of
  those steps extends both files together). No existing editor text moves (R2).
- **Dependencies:** [P1]
- **Tests:** AC5 (parse, equal key sets ≥ 1, non-empty values, identical placeholder sets; the
  referenced-keys check over `wizard.py`/`console.py` is a collector written here and run again in
  P12–P15 as keys appear — a key referenced by source is a string literal passed to `sentence`/a `_t`
  helper, collected by AST). AC6 discovery half: copying `en.json` to a fixture directory as `de.json`
  offers `de` with 0 source files changed; deleting 1 key removes it; a mismatched placeholder removes it.
- **Risks:** A key collector that misses dynamically built keys gives a false 0-missing: forbid dynamic
  key construction in `wizard.py`/`console.py` except the reason-token mapping, whose coverage AC20
  checks separately (P13). The strings must not contain `kick`/`youtube` (AC39 still scans them) nor
  vendor names in non-exempt files.

### P4: The four ready-made profiles and their generator (R3; AC7 data half, AC8, AC10)
- **Files:** [`core/config_ui/profiles/chat_qa.json`, `core/config_ui/profiles/channel_points.json`, `core/config_ui/profiles/spoken_keyword.json`, `core/config_ui/profiles/silent.json`, `core/config_ui/wizard.py`, `tests/test_first_run.py`]
- **Description:** Author the four profiles (DD5) in the order chat Q&A, channel points, spoken keyword,
  silent, each declaring: its string-table name/description keys, its parameters (companion name;
  reward id; wake word + detector + transcription settings; none), its credential references (`${NAME}`,
  exactly those the enabled modules' settings schemas require), the platform scopes it needs, the
  platform validation and reward-list endpoints (data, DD1), the dry-run fake event kind and the
  expected dry-run outcome (`completed` status / `not triggered`), and a template built only from shipped
  module names, action names, trigger types and event kinds (phase 8's `reward` trigger on `reward.id`,
  `audio_input`'s `wake_word`; silent: platform input connected, every trigger off, no model module
  settings exercised). In `wizard.py` add `load_profiles(directory)` and `generate_profile(profile,
  parameters, identities) -> dict` plus `render_profile_yaml(document)`: every credential appears only as
  `${NAME}` and is listed under `secrets`; broadcaster/bot identities come from the validation answer
  (P10) as parameters, never typed. The shipped `*.example` files are not touched.
- **Dependencies:** [P1, P3]
- **Tests:** AC8 — for each profile generated with fixture parameters into tmp: 0 fixture credential
  values in the file; every `${NAME}` listed under `secrets`; `core.main.check_config` with the stored
  credentials as `environ` returns exit 0 with 0 diagnostics and 0 sockets (the AC33 guard is the
  socket check). AC10 — the 4 `*.example` files byte-identical to `cb0f5b1` (compared with a baseline
  fixture under `tests/fixtures/baselines/` or the existing phase-8 baseline helper — no `git` process);
  each generated `enabled_modules` ⊆ the 21 shipped manifest names, 0 action names/trigger types/event
  kinds not declared by a shipped manifest. AC7 data half: 4 profiles loaded in the stated order, each
  with name and description keys present in both languages.
- **Risks:** A template that passes `--check-config` only with a module the shipped runtime lacks
  violates R3 — validate against the real manifests, not a fixture directory. The silent profile must
  still connect the platform input with every trigger off: confirm the twitch manifest accepts an
  explicit "off" policy (else express "off" as a configured policy that never matches, and say so in the
  profile's description key). Spoken keyword's detector/transcription settings must match phase 8 R7's
  schema exactly (AC16 uses a malformed detector to provoke exit 2).

### P5: Credential store and wizard state file (A1, A7, R4; AC11 storage half, AC30 state-file clause)
- **Files:** [`core/config_ui/wizard.py`, `tests/test_first_run.py`]
- **Description:** `CredentialStore(profile_path, platform, environ)`: resolves the A1 directory via
  `locations.py`; on POSIX creates it 0700 if absent and refuses (no write, a value-free refusal reason
  for screen 4) when it is not owned by the user or is group/other-writable; on Windows refuses when
  `LOCALAPPDATA` is unset or not an existing directory; writes `<profile-file-name>.credentials.json`
  atomically (temp created 0600 via `os.open(..., 0o600)`, then `os.replace`), holding only `{NAME:
  value}` for names the profile lists under `secrets`; reads/replaces it only when the profile's state
  file records its digest, otherwise neither reads nor writes and returns a refusal naming the path.
  `WizardState(profile_path)`: `<base>.wizard.json`, 0600 on POSIX, atomic replace, holding profile
  choice + non-secret parameters, language, per-screen pass records for the current process only
  (successes are never persisted as passes beyond screen 1, A7), the digest of each file screen 4 wrote,
  and `completed`. Ownership check: `owns(path)` is true only while the file's bytes hash to the recorded
  digest. Never stores a credential value (an assertion in `save()` compares against the store's values).
- **Dependencies:** [P1, P4] (P4 first: both steps edit `core/config_ui/wizard.py`; P5 adds its classes
  after P4's generator so the file has one defined baseline)
- **Tests:** AC11 storage half — file exists under the per-user dir with mode `0o600`, holds the value
  under its reference name and no profile content; `a.yaml`/`a.yml` → 2 distinct files, the other left
  untouched; platform forced to Windows with `LOCALAPPDATA=<tmp>` → under `<tmp>/twitch-ia-compagnon/`;
  group-writable directory (POSIX) or `LOCALAPPDATA` unset (Windows) → no credential file and no file in
  the profile or per-user directory holds the value; a pre-existing credential file whose digest the
  state does not record is byte-identical afterwards and never opened for reading (spy on `open`).
  State file never contains a stored or submitted credential value (AC30 clause).
- **Risks:** Creating the file then `chmod`-ing leaves a window: create with mode at `os.open`. The
  umask can widen nothing below 0600 but can narrow the directory; check ownership with `os.stat`
  (`st_uid == os.getuid()`) only on POSIX. Windows behaviour is simulated by forcing the platform value;
  the code must not call Windows-only APIs that are missing on Linux during tests.

### P6: `--config` optional, extended startup collisions, first-run landing and the browser opener (R1; AC1, AC2, AC3; allowlisted `test_a_missing_config_or_bad_port_exits_non_zero`)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`, `tests/test_first_run.py`, `tests/fixtures/phase9/editor_base_page.html`]
- **Description:** Before changing any render, capture the pre-phase editor base-page markup for a fixed
  fixture (tokens, nonces and field keys pinned by monkeypatching `secrets`) into
  `tests/fixtures/phase9/editor_base_page.html`. Then: `_parser()` makes `--config` optional;
  `UISettings.from_argv` resolves an absent `--config` to `locations.default_profile_path(sys.platform,
  os.environ)` and `UISettings` gains `wizard_path`, `credential_path`, `instance_path`, `feed_path`
  properties (canonical forms). `startup_checks` adds, after the existing checks and in their style,
  collision refusals for the wizard state, credential and instance files and the feed path against base,
  overlay, status and each other (one problem per collision, naming role, path and the collided file,
  never content). `main()` gains keyword-only `opener` (default: `webbrowser.open`) and `serve`
  (default: module-level `serve`) parameters; it creates the default profile's parent directory 0700 if
  absent (only when `--config` is absent), prints the access URL once, then calls the opener once in a
  `try` whose failure is logged value-free (the URL is never in a log record) and never stops the UI.
  The `/` handler routes the access URL's landing: first run (base absent) or an unfinished state →
  a redirect to `/wizard`; otherwise a redirect to `/console` (default location) or the unchanged editor
  (`--config`); DD7's link. P6 only owns the **routing decision** (`landing_target(settings, state)` plus
  the redirect issued after the access URL creates the session): the `/wizard` and `/console` handlers
  do not exist yet at this point, so P6 asserts the redirect's `Location` and the session cookie, never
  the target page's content. The end-to-end landing (the rendered wizard screen 1 / the rendered
  console) is asserted by P12 and P13 once those handlers exist.
  Adapt `test_a_missing_config_or_bad_port_exits_non_zero` per the allowlist: keep both bad-port
  assertions and assert a no-argument start with a colliding default (e.g. `--status-file` pointing at
  the default `<base>.wizard.json`) exits non-zero with `serve` never called.
- **Dependencies:** [P1, P5]
- **Tests:** AC1 in full (default path, 0 problems, `--host 0.0.0.0` refusal, the six collision cases:
  status and overlay each set to `<base>.wizard.json`, `<base>.instance.json` and the credential file,
  each with fixture content, exactly 1 problem naming role/path/collided file with 0 occurrences of the
  content, `main([], serve=fake)` non-zero and `serve` 0 calls). AC2 routing half (first run: the access
  URL answers with a session cookie and a redirect whose `Location` is `/wizard`; an unfinished state
  file: `Location` `/wizard`; a profile at the default location with no `--config`: `Location`
  `/console`; `--config`: the editor itself, whose markup equals the captured fixture modulo the one
  added link — fully verifiable here because the editor exists). The wizard/console halves of AC2 are
  completed in P12 (wizard page rendered after following the redirect) and P13 (console page rendered
  after following the redirect). AC3 (opener exactly once with `ui.access_url`,
  URL printed exactly once, 0 log records with the token; a raising opener → exit 0, URL printed). The
  existing `main([...])` tests of `tests/test_config_ui.py` still pass unmodified (their default opener
  is refused by the AC33 guard and swallowed by design — asserted once in `tests/test_first_run.py`).
- **Risks:** Existing tests calling `main` now reach the real `webbrowser.open`; under the AC33 guard
  its process start raises and is swallowed. If a `webbrowser` path on the test machine opens a browser
  without going through a guarded entry point, it could open a real browser: the step checks the Linux
  `webbrowser` code paths, injects the opener in every new test, and records the residual risk in the
  commit message. Production code gets no test-awareness. Creating the default directory must not happen for `--config` runs (behaviour
  unchanged, A2). The base-page fixture must be captured from the unmodified code in the same step, first.

### P7: Stored credentials join the secret set and every launched environment (R4; AC12)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_first_run.py`]
- **Description:** `ConfigUI.__init__` builds `self.environ` as the UI's environment overlaid with the
  credential store's values when the store is usable for this profile (stored wins for the names it
  holds), so the existing set/unset judgement counts stored values as set; `self.secret_values` is seeded
  with the stored values (and their redaction forms via `_redaction_forms`) so `_RedactionFilter`,
  `_report_tail` and every render redact them. The `Supervisor` is constructed with
  `environ=` that merged environment plus `FEED_FILE_VARIABLE` → `settings.feed_path` (never the access
  token). Check (`_default_checker`) and Apply already take the UI's environment — confirm both read
  `self.environ` and pass it through. After screen 4 writes new values (P12) the UI refreshes both sets.
- **Dependencies:** [P2, P5, P6]
- **Tests:** AC12 — store `TWITCH_ACCESS_TOKEN=stored`, UI environment `inherited`: the env given to a
  fake supervisor `popen`, to `check_config` and to the dry-run seam each contains `stored`; the editor
  renders that credential as set; a log record and an Apply stderr tail containing `stored` render
  redacted. The supervisor env contains the feed path and 0 occurrences of the token.
- **Risks:** `ConfigUI` is constructed in 61 test sites with explicit `environ`; the merge must be a
  no-op when no credential file is usable for the profile (tests' fixtures have none), so existing
  assertions on `environ` identity are not broken — check for `ui.environ is environ` style assertions
  and keep identity when the store is empty.

### P8: The instance record, the `/instance` route and the second launch (R1; AC31, AC4 `/instance` row)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`, `tests/test_first_run.py`]
- **Description:** `ConfigUI` mints `instance_marker = secrets.token_hex(16)` (128 bits, distinct from
  the token). Add route `/instance` (GET only): after the Host and method guards, it checks the access
  token from the query exactly as the access URL does (constant time, same refusal status), creates no
  session and answers JSON `{"marker", "version"}` with `locations.product_version()`. In `main()` add an
  injectable `instance` seam bundle (`liveness`, `probe(port, url, timeout)`, `clock`, `sleep`,
  `platform`, `publish` hooks for the race fake): before serving, read `<base>.instance.json`.
  Classification exactly as R1 states — incomplete (unparseable, missing or wrong-typed field for the
  platform) or pid not live → stale; POSIX live pid → probe with the recorded token, each attempt ≤ 2 s,
  retried every 0.5 s, none started after 10 s since the first; recorded marker → confirmed (same version:
  opener with the recorded URL, "already running" line, exit 0; other version: report naming both, exit
  non-zero, nothing opened); other marker → stale; no confirming answer → report (record path, port fact:
  answering-but-unauthenticated vs not answering, cause, next action, 0 URLs), record kept byte-identical,
  exit non-zero; non-POSIX live pid → "running but not answering" report, no probe, kept, non-zero.
  Stale removal: rename to a launch-unique name, delete only if the bytes equal the judged ones, else
  restore with a no-replace rename and re-read. Publication: temp file (0600 on POSIX, from `os.open`),
  then `os.link` to the record name (fails if present) and unlink the temp; on Windows `os.rename`
  (fails if present); a lost race re-reads and behaves as a second launch. The record holds pid, port,
  marker and, only on POSIX, the access URL. A clean exit (a `finally` around `serve`) removes it only
  while it still holds this instance's marker. All report sentences come from the string table (P3).
  Extend the `test_ac35` route-set assertion with `/instance` (allowlisted entry; final set in P15).
- **Dependencies:** [P3, P6, P7] (P7: both edit `core/config_ui/__init__.py`; P8 builds on P7's
  `ConfigUI.__init__`)
- **Tests:** AC31 in full with fakes (opener, serve, liveness, probe, clock): confirmed same version
  (opener 1× with recorded URL, line printed 1×, exit 0, `serve` 0); first attempt > 2 s and a later one
  within 10 s confirms; record port 8766 vs `--port 8765` → 8766 URL; raising opener → `serve` 0, line
  printed; all attempts refused / > 2 s / wrong-token until 10 s → > 1 attempt, each ≤ 2 s, none after
  10 s, `serve`/opener 0, record byte-identical, report naming path and the port fact, 0 URLs, non-zero;
  stale (a)–(d) → "did not stop cleanly", record replaced by a fresh one with this marker, opener 0 with
  the old URL, `serve` exactly 1, no exception; non-POSIX publication without token and second-launch
  report; version mismatch; failure between temp write and publication → no file under the record name;
  the publication race → 1 `serve`, loser opens the winner's URL; mode `0o600`; token 0 times in logs,
  diagnostics, response bodies (probe's included), the child's env and the status record; clean exit
  removes the record. AC4 `/instance` row: wrong Host, wrong method, missing/wrong token (status as the
  access URL's wrong token, 0 sessions, 0 marker bytes); a good GET answers 200 with exactly marker and
  version, 0 sessions.
- **Risks:** `os.link` is unsupported on some filesystems (FAT, some network mounts): treat `OSError`
  other than `FileExistsError` as a publication failure with a value-free diagnostic and refuse to serve
  rather than fall back to a replacing rename. The real probe uses an HTTP client — it must only ever be
  constructed in production; tests inject it (AC33). Liveness on POSIX reuses `process_alive`. A
  record removed by the clean-exit path while another launch is reading it must not crash that launch
  (treat as "no record").

### P9: The OS launcher writer and `--install-launcher` (R1, A3; AC27)
- **Files:** [`core/config_ui/launcher.py`, `core/config_ui/__init__.py`, `tests/test_first_run.py`]
- **Description:** `launcher.py`: `write_launcher(platform, *, directories, executable, config=None) ->
  Path` writes one file — Linux `.desktop` under the user applications directory
  (`$XDG_DATA_HOME/applications` or `~/.local/share/applications`), Windows `.cmd` under the Start Menu
  programs folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs`), macOS `.command` under
  `~/Applications` — whose command runs `executable -m core.config_ui` (plus `--config <path>` only when
  one was given), carrying a recognisable marker line, no token and no secret; mode owner-writable, not
  group/world-writable (0o755 for the executable `.command`/`.desktop` forms, 0o644 for `.cmd`); an
  existing file at the path is replaced only if it carries the marker line, otherwise refused (value-free
  reason). Directories are injectable. `_parser()` gains `--install-launcher`; `main()` handles it after
  argument parsing and before any startup check that could serve: write, print the path, exit 0 (or
  non-zero on refusal), `serve` never called. The launcher's display name comes from `locations.py`'s
  product name (the launcher module itself names no platform).
- **Dependencies:** [P1, P6, P8] (P8: both edit `main()` in `core/config_ui/__init__.py`; the
  `--install-launcher` exit is placed relative to P8's instance judgement)
- **Tests:** AC27 — for each forced platform (linux/win32/darwin) with the directory redirected to tmp:
  exactly 1 file of the right suffix, naming `sys.executable` and `-m core.config_ui`, 0 occurrences of the
  token and of a stored credential value, not group/world-writable on POSIX, `serve` 0, exit 0; running
  again replaces it (1 file); a pre-existing file without the marker line is byte-identical and exit is
  non-zero.
- **Risks:** Paths with spaces (macOS "Application Support", Windows user names) must be quoted in each
  launcher format (`Exec=` escaping rules for `.desktop`, `"…"` in `.cmd`, shell quoting in `.command`).
  `--install-launcher` must not trigger the instance-record logic (P8) or create the instance record.

### P10: Platform and model validators behind injectable transports, with the plain-language failure map (R5 screens 2–3, D3; AC14, AC15, AC17 validator half)
- **Files:** [`core/config_ui/wizard.py`, `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json`, `tests/test_first_run.py`]
- **Description:** In `wizard.py`, `validate_platform(profile, token, client_id, *, transport, clock,
  timeout=10)`: one GET to the profile's validation endpoint; outcomes map to distinct failure tokens —
  `connection_refused`, `timeout` (fake clock), `unauthorized` (401), `client_id_mismatch`,
  `missing_scope` (names the missing scope names, which are not secrets), `malformed_answer` — or a
  success carrying login and user id (broadcaster and bot identity filled from the user id). For channel
  points, `list_rewards(profile, token, client_id, user_id, …)` (one bounded read) → titles + ids, or
  `no_rewards` ("create the reward first"), or the same transport failures. `validate_model(endpoint,
  model, api_key, *, transport, clock, timeout=30)`: exactly one chat-completions request; outcomes
  `connection_refused`, `timeout`, `unauthorized` (401/403), `not_found` (404), `not_a_completion`
  (shape), `empty_answer`, or success; the silent profile short-circuits to "not needed" with 0 requests.
  Transports are protocols (production: a thin aiohttp adapter run via the existing socket-free loop
  helper pattern with a real connector only outside tests); every failure resolves to a string-table
  cause + next action pair, never an exception text or submitted value. Add those keys to both string
  files.
- **Dependencies:** [P3, P4, P5, P9] (P5: both edit `core/config_ui/wizard.py`; P9: the previous
  writer of `tests/test_first_run.py` in the shared-file chain)
- **Tests:** AC14 (each of the 6 outcomes a distinct cause with a next action and *next* closed; 200 with
  login `streamer_x` and user id `123` → `streamer_x` shown, identities `123` in the generated profile;
  2 rewards → 2 titled choices; empty list → its own cause). AC15 (6 outcomes distinct; success after
  exactly 1 request; silent → not needed, 0 requests). AC17 validator half: each failure's rendered text
  holds 0 `Traceback`, `File "`, exception class names or submitted values (a sweep over the cause
  sentences rendered with every token). AC5 key check re-run.
- **Risks:** The timeout must be enforced by the validator's own bound, not the transport's, so a fake
  clock drives it deterministically. A malformed but 200 answer whose fields have wrong types must map to
  `malformed_answer`, not raise. No validator may log the request URL with query credentials.

### P11: The dry-run entry and its run record (R5 screen 5, DD6; AC9, AC29 record half)
- **Files:** [`core/main.py`, `tests/test_first_run.py`]
- **Description:** Add `DryRunRecord` (run_id, outcome, reason, origin, event) and `async def
  dry_run(config_path, event, *, environ, model_transport=None, clock=None, timeout=60.0)` to
  `core/main.py` as DD6 describes: same load/validation as `check_config`; the runtime assembled with
  `_assemble_runtime`; every enabled module activated with its `_session_factory` seam set to an inert,
  socket-free session (the brain's model transport is the injected one, or the real one in production);
  a recording provider bound in place of each action the platform input module provides; the fake event
  evaluated by the context's `TriggerEngine` under the profile's resolved policy and, when accepted,
  published with the input's event shape so the brain's admission scheduler records a run; the record's
  run id and outcome are read from the scheduler's record for that event (completed status), or, when
  the trigger rejects, `not_triggered` with the rejection's reason token and 0 model requests. Bounded
  by `timeout` on the injected clock; the runtime is always closed. The record's origin is `dry_run` and
  names the fake event. `core.main`'s command line is unchanged.
- **Dependencies:** [P2, P4, P10] (P2: both edit `core/main.py`; P10: the previous writer of
  `tests/test_first_run.py`)
- **Tests:** AC9 — with a fake model answering `"bonjour"`: chat Q&A mention → 1 run started and 1
  completed (success), answer `bonjour`, 0 messages to the platform (the recording provider holds the
  proposal, nothing sent); no mention → 0 runs, 1 trigger rejection with its reason; channel points
  picked reward id → 1 run, other id → 0; spoken keyword wake word → 1 run; silent chat message → 0 runs,
  1 rejection with reason, 0 model requests. AC29 record half — for the 3 triggering profiles the run id
  equals the scheduler record's and the feed's (feed path supplied) run started/completed entries; silent
  → `not_triggered` + reason token; origin `dry_run` naming the event.
- **Risks:** Replacing the platform module's network seam must not suppress its trigger declarations,
  which the registry needs to resolve the policy (activate the module's declaration path, not its
  transport). Modules that spawn processes (audio_input's detector/transcription commands) must not
  start one during a dry run — feed the spoken-keyword event after the wake-word decision through the
  hooked-type path with a fake transcript, and assert 0 process starts (AC33 guard). The core must stay
  platform-free (DD1): identify the platform module from manifest input ownership, never by name.
  Shutdown on `ManualClock` may report "timed out" (memory: assert the outcome).

### P12: The five wizard screens, server-side gating, regeneration and re-entry, `/wizard` and `/language` (R5, A7, R2, R4; AC6 choice half, AC7, AC11 response sweep, AC13, AC16, AC17, AC28, AC29 screen half, AC30)
- **Files:** [`core/config_ui/wizard.py`, `core/config_ui/__init__.py`, `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json`, `tests/test_config_ui.py`, `tests/test_first_run.py`]
- **Description:** In `wizard.py`, a `Wizard` controller over `WizardState`, `CredentialStore`, the
  validators (P10), the checker and the dry-run seam (P11), all injected by `ConfigUI`. Screens: (1)
  language + profile + parameters (4 options in order, names/descriptions from the table); (2) platform
  credential (P10), identities from the answer, reward pick for channel points; (3) model (P10) or "not
  needed"; (4) write the generated profile (refuse if a profile appeared at the target after first-run
  detection, or a file exists that the state does not own byte-identically) and the credential file where
  A1 allows, else state the refusal and keep *next* closed; run `check_config` with the merged
  environment, naming an offending field path in plain language on exit 2; read-only YAML view
  (non-editable markup, 0 form controls posting profile content, redacted); (5) dry run → success only on
  a record with run id, outcome and `dry_run` origin whose outcome is the profile's expected one; then
  mark `completed` and link to the console. Gating: a request for screen n+1 is refused (re-rendering
  screen n) unless screen n's success is recorded for the current inputs' fingerprint; changing an input
  of a validated screen invalidates it and every later one. **Fingerprints are value-sensitive without
  persisting secrets:** `ConfigUI` mints a per-process key `secrets.token_bytes(32)` held only in memory;
  a screen's fingerprint is SHA-256 over its non-secret inputs plus, for each credential input,
  `hmac.new(key, value, sha256)` of the effective value (a submitted value, or the stored value when the
  input is left empty as "kept"). Replacing one non-empty token or API key with a different one changes
  the HMAC, so the screen and every later one are invalidated exactly like any other input change; a
  resubmission of the same value does not invalidate. The fingerprint table lives in memory only: the
  state file persists only screen 1's pass (whose inputs are non-secret, A7) and never a credential
  value, HMAC or fingerprint derived from one; a new process has a new key, which matches A7 (screens
  2–5 must pass again anyway). Re-entry per A7: only screen 1 resumes as
  passed; screens 2–5 must pass again; credentials show "kept" and are never re-rendered. Completed →
  every wizard route redirects to the console. Credential inputs render empty with a "kept" state;
  no response, redirect or link carries a stored or submitted value; failures render a cause + next
  action only. In `__init__.py` register `/wizard` (GET, POST) and `/language` (POST, persists the
  choice in the state file, DD3) behind the existing guard chain; the wizard never calls the supervisor.
  Extend the `test_ac35` route set with `/wizard`, `/language` (allowlisted).
- **Dependencies:** [P5, P6, P7, P8, P9, P10, P11] (P8/P9: earlier editors of
  `core/config_ui/__init__.py`, and P8 already extended the `test_ac35` route set with `/instance`, the
  baseline P12 extends)
- **Tests:** AC2 wizard half (first run: following the P6 redirect with the session cookie renders
  screen 1 of the wizard, status 200, 4 profile options). Credential replacement (R5): pass screens 2–3
  with token `tok_A`, then submit screen 2 with `tok_B` → screens 2–5 recorded as not passed, advancing to
  3 is refused until screen 2 passes again, the platform validator is called again exactly once with
  `tok_B`; resubmitting `tok_B` unchanged after it passed keeps 2–5's records; the same for the model API
  key on screen 3 (invalidates 3–5, not 2); the state file's bytes contain neither `tok_A`, `tok_B` nor
  the hex or base64 form of any HMAC/fingerprint computed with them (the test recomputes them through
  the UI's key). AC6 choice half (`fr` on screen 1 renders the next page in French; survives a new
  `ConfigUI`). AC7 (exactly 4 options in order, both languages). AC13 (for each n in 1..4, advancing
  before n's success is refused, re-renders n, 0 calls on the next validator; changing a validated input
  invalidates n..5). AC16 (profile appeared after detection → refused, byte-identical; spoken keyword with
  a malformed detector → exit 2, field path named, *next* closed; read-only view has 0 posting controls;
  0 new routes accept YAML content; editor routes unchanged). AC17 (every AC14–AC16 failure and a dry run
  > 60 s on the fake clock: 0 `Traceback`, `File "`, exception class names, submitted values; 0
  supervisor `start` over a full successful pass). AC11 response sweep (profile, overlay and every
  response body including the YAML view and screen 2's wrong-client-id re-render: 0 occurrences of the
  value, URL-encoded, HTML-escaped, JSON-escaped; empty "kept" input; screen 4 names the Windows
  directory and why; refusal keeps *next* closed). AC28 (companion-name change after screen 4
  invalidates 1–5, re-passing replaces the profile with the new name, 1 profile + 1 credential file; an
  externally appended byte → screen 4 refuses, file byte-identical, cause names the editor; completed →
  new `ConfigUI` lands on the console). AC29 screen half (each malformed/absent record case and the 60 s
  timeout leave screen 5 open, state not completed, a cause shown, retry possible; silent succeeds on its
  record). AC30 (a)–(e) with fake endpoints, counting exactly 1 platform validation, 1 model request and
  1 dry-run call in the new instance. AC5 key check re-run.
- **Risks:** This is the largest step; keep screens as small render/validate pairs and the gating as one
  fingerprint table so AC13 holds by construction. Fingerprints use a keyed in-memory HMAC of each
  credential value (never presence alone, which would not see a replaced token, and never a bare hash,
  which would let a short secret be guessed from a leaked digest); the key and the table must never be
  written to the state file, a log record or a response — the replacement test sweeps the state file
  for them. The guard order (Host, session, method, Origin, CSRF) is
  inherited from `ConfigUI.handle`; do not add a wizard-specific bypass for the first GET (the access URL
  creates the session, then redirects). Screen 4's "appeared after detection" test needs the first-run
  decision recorded at UI construction.

### P13: The console page, the feed poll and plain-language reasons (R7, R2; AC20, AC21)
- **Files:** [`core/config_ui/console.py`, `core/config_ui/__init__.py`, `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json`, `tests/test_config_ui.py`, `tests/test_console.py`]
- **Description:** `console.py`: `read_feed(path, after=cursor)` (bounded read of the P2 file, tolerant of
  a missing or partly written file), `render_console(state, feed, health, grants, language, table)` with
  the runtime state (running under this UI / not running / another unsupervised process, from the status
  record and the supervisor's child), each enabled module ready/degraded with cause and next action,
  feed entries newest first, a link to the editor, and `reason_sentence(token)` mapping every reason
  token to a table sentence (unknown → the generic sentence naming the token). `__init__.py` registers
  `/console` (GET) and `/feed` (GET, session-gated JSON poll with `?after=<sequence>`), both behind the
  existing guards; everything rendered passes through the R4 redaction. Add every reason sentence to
  both tables. Extend the `test_ac35` route set with `/console`, `/feed` (allowlisted).
- **Dependencies:** [P2, P3, P6, P7, P12] (P12: both edit `core/config_ui/__init__.py`, the two
  string tables and the `test_ac35` route set in `tests/test_config_ui.py`; P13 extends P12's set;
  P6: the landing redirect P13's AC2 half follows)
- **Tests:** AC2 console half (a profile at the default location, no `--config`, finished or absent
  state: following the P6 redirect with the session cookie renders the console page, status 200, with
  the runtime state and the editor link). AC20 — collect every `REASON_*` constant exported by `core/triggers.py`, `core/admission.py`,
  `core/actions.py` and every reason token emitted on `input.trigger.rejected`,
  `brain.admission.rejected`, `brain.run.completed` and refused actions by the shipped modules (AST/regex
  over the sources, in-process — no `grep` process); each has a sentence in both languages; `zz_unknown`
  renders a non-empty sentence containing it. AC21 — fixture feed (arrived, run started, run completed,
  `self_authored` rejection, `audio_output` degraded with cause) renders exactly 5 items newest first
  (degraded first, arrived last), the rejection in French when `fr` is chosen, the degraded module with
  cause and next action; the poll with the newest cursor returns 0 entries.
- **Risks:** Reason tokens emitted as literals inside modules (not constants) are easy to miss: the
  collector must scan string literals passed as `reason=` in the shipped modules and the test must fail
  on a new uncovered token. Module causes are module-owned text (memory: failures name the module only;
  the cause is on `module.degraded`) — render it as quoted data, redacted, with a table next action.

### P14: Start and Stop through the supervisor, with the cause / raw-detail split (R7, R4, R2; AC22, AC32)
- **Files:** [`core/config_ui/console.py`, `core/config_ui/__init__.py`, `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json`, `tests/test_config_ui.py`, `tests/test_console.py`]
- **Description:** Register `/start` and `/stop` (POST, CSRF-guarded). Start launches through the existing
  `Supervisor.start` only when no live process holds the status record (`read_status` + the liveness
  probe); otherwise "another instance is running". Stop calls `Supervisor.stop` only on the child this UI
  launched. A child that exits during startup renders (a) the cause — a table sentence + next action in
  the chosen language, outside any disclosure, containing nothing of the tail — always, even with an
  empty tail; and (b) the raw detail — the existing bounded `_report_tail`, redacted, untranslated,
  inside one `<details>` element with no `open` attribute and a table label. No wizard response renders a
  disclosure. Extend the `test_ac35` route set with `/start`, `/stop` (allowlisted).
- **Dependencies:** [P7, P13]
- **Tests:** AC22 — fake supervisor: Start with no live record → `start` 1× with the profile's launch
  argv and the feed path in the env; live pid not launched by this UI → `start` 0, "another instance is
  running"; Stop → `stop` 1× on our child, 0 otherwise; a child exiting 1 during startup → AC32's two
  surfaces. AC32 — tail with a stored credential value and `Traceback (most recent call last):` → cause in
  French when `fr`, outside the disclosure, 0 tail characters; the tail only inside one closed
  `<details>`, credential replaced by the redaction marker, 0 occurrences of it or its encoded forms
  anywhere; empty tail still renders the cause; no wizard response contains a `<details>` raw detail.
- **Risks:** "0 characters of the tail" in the cause: the cause sentence must never interpolate any
  tail-derived value (the cause token is chosen from the exit status/phase only). Start racing an Apply
  must use the existing mutation lock.

### P15: Grant listing and Revoke through the managed overlay; the final route set (R7, A6; AC23, AC4 full sweep; allowlisted `test_ac35`)
- **Files:** [`core/config_ui/console.py`, `core/config_ui/__init__.py`, `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json`, `tests/test_config_ui.py`, `tests/test_console.py`]
- **Description:** The console lists the merged configuration's `actions` rules (rule id, action,
  principal, destination). `/revoke` (POST, CSRF) removes one rule by id through the existing overlay
  writer (`_write_overlay`, never the base): the overlay's `actions` is set to the merged list minus that
  rule (the overlay replaces lists, so the removal is expressed as the full remaining list); with a
  supervised child running it performs exactly one supervised restart (the existing Apply path) and
  shows its report; otherwise the removal takes effect at next start. No route or control adds or
  widens a grant, or changes a policy, admission bound or module setting. Finalise the `test_ac35`
  route-set assertion as exactly the 7 existing routes plus `/wizard`, `/language`, `/console`, `/feed`,
  `/start`, `/stop`, `/revoke`, `/instance` (allowlisted adaptation; the test's secret sweep now covers
  every new route).
- **Dependencies:** [P8, P12, P13, P14]
- **Tests:** AC23 — profile granting `brain-reads-chat` and `brain-reads-users`: both listed; Revoke on
  `brain-reads-users` leaves the base byte-identical, the merged `actions` lists only `brain-reads-chat`,
  with a supervised child exactly 1 restart and its report; afterwards a `users.read` call (through the
  executor over the merged config) is refused `no_applicable_rule` and the console renders that sentence;
  the new route table entries are exactly the 8 listed. AC4 full sweep in `tests/test_console.py` — for
  every new route: wrong Host, no session, wrong method, foreign Origin (state changes), missing/wrong
  CSRF (state changes) refused with the existing routes' status codes, 0 validator, supervisor,
  file-write or feed-read calls on the fakes (the `/instance` row from P8 re-run here).
- **Risks:** List-replacement semantics of the overlay must be confirmed in `core/overlay.deep_merge`
  (if lists merge rather than replace, the revocation must be expressed differently and the step says
  how). A revoked rule referenced by `${…}` destinations must be compared on the unresolved document.
  Existing `test_config_ui` guard-order tests use the route table; adding routes must not change their
  expected codes.

### P16: Package data for strings and profiles; the installed-distribution node asserts them (R8; AC26)
- **Files:** [`pyproject.toml`, `tests/test_profiles.py`, `tests/test_first_run.py`]
- **Description:** Add `"core.config_ui" = ["strings/*.json", "profiles/*"]` under
  `[tool.setuptools.package-data]`; runtime dependencies stay exactly `aiohttp` and `PyYAML`. In
  `test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` add assertions only: the
  install target holds `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json` and the 4
  profile files under `core/config_ui/profiles/`, each byte-identical to the checkout's; the 21-manifest
  count, the node's identity and `conftest.A2_REAL_NODES` are unchanged. The pyproject line and the
  dependency set are asserted by a new in-process node in `tests/test_first_run.py` (it reads
  `pyproject.toml` with `tomllib`; it spawns nothing).
- **Dependencies:** [P3, P4, P12] (P12: the previous writer of `tests/test_first_run.py`)
- **Tests:** AC26 — pyproject parse: dependencies exactly the two, the `core.config_ui` line exactly
  `["strings/*.json", "profiles/*"]`; the A2 install node passes (needs `pip` in `.venv` — memory:
  `ensurepip`; setuptools distutils shim note); `test_install_test_skips_only_when_pip_is_unavailable`
  still holds; the full suite passes under the AC33 guard with 0 new exemptions and 0 new
  process-spawning nodes.
- **Risks:** `_pristine_checkout`'s ignore function may skip non-`.py` files under `core/` — check
  `_ignore_unpackaged` and confirm JSON under `core/config_ui/` is copied, else the byte-identity check
  fails for the wrong reason (do not change the ignore rule beyond what the install needs; flag if it
  must change). `core*` package discovery must include `core.config_ui` (it does, as a package).

### P17: The first-run guide pair and the documentation index (R8; AC24; allowlisted `test_the_discovered_pairs_are_exactly_the_declared_guides`)
- **Files:** [`docs/first-run.md`, `docs/first-run.fr.md`, `docs/README.md`, `tests/test_docs_bilingual.py`, `tests/test_first_run.py`]
- **Description:** Write the English guide (normative) and its French translation with the same shape,
  commands and identifiers: the install path (pointing to `docs/installation.md`) and
  `--install-launcher` (A3b), starting from the launcher, the default location on all 3 platforms, the
  credential file by name (`<profile-file-name>.credentials.json`, `profile.yaml.credentials.json` at the
  default) and its per-user directory on each platform (`%LOCALAPPDATA%` on Windows), that a terminal-
  started runtime does not read it (A1), the five screens, the console (feed, Start/Stop, raw detail,
  Revoke), and moving on to the editor. Link the pair from `docs/README.md`. Add `first-run` to
  `GUIDES` (allowlisted).
- **Dependencies:** [P12, P13, P14, P15, P16] (P16: the previous writer of `tests/test_first_run.py`)
- **Tests:** AC24 — `tests/test_docs_bilingual.py` discovers the pair with `first-run` declared, shape and
  identifier checks pass; a test (in `tests/test_first_run.py`) asserts the guide names the 3 default
  locations, the credential file name and the 3 per-user directories, and that `docs/README.md` links
  both files.
- **Risks:** The bilingual identifier check compares code spans: every command and path must be
  identical in both files. Guides are outside `core/`, so platform names are allowed there.

### P18: The trial-record template and its approval checker (R8, A3b; AC25)
- **Files:** [`docs/campaigns/phase9/trial-record.md`, `tests/test_first_run.py`]
- **Description:** Write the template with the 13 labelled fields of R8 (date, commit, machine and
  platform, install method (who, means, terminal used), install duration, profile, trial start,
  per-screen times ×5, first-run time, elapsed minutes, terminal/YAML touched, outcome, limits — the
  install listed among the limits) and the criterion in Amendment A1's words. Add a parser
  `trial_record_problems(text)` in the test module that returns every reason AC25 refuses an approval
  (absent/empty/non-value install method, install duration or elapsed minutes; platform not naming
  Linux, macOS or Windows; any other field empty; terminal/YAML not "no"; per-screen and first-run times
  not non-decreasing or before the trial start; elapsed ≠ first-run − start ±1 min; elapsed ≥ 15). The
  filled record is the operator's: after the real trial the operator fills it, and the gate runs the
  checker on it; the template itself is asserted to carry every label and the criterion sentence.
- **Dependencies:** [P17] (P17: the previous writer of `tests/test_first_run.py`; the template
  itself is independent)
- **Tests:** AC25 — the template carries the 13 labels and the A1 sentence; the checker accepts a
  conforming fixture record and refuses each violation listed above in its own fixture (one problem
  each); while the record is the unfilled template the checker reports it as not approvable (the test
  asserts that, it does not fail the suite).
- **Risks:** The fifteen-minute claim rests on the record only (D1): no test may claim it. Time parsing
  must accept the single format the template prescribes (ISO local time with offset) and refuse others
  rather than guess.

### P19: Whole-suite run and full-branch review gate (all of R1–R8; AC1–AC32; DD1–DD9)
- **Files:** [`docs/campaigns/phase9/full-branch-review.md`, `docs/campaigns/phase9/summary.md`]
- **Description:** Run the full suite under the AC33 guard and record the result. Then review the whole
  branch diff `b7ec50d..HEAD` together, not commit by commit: per-commit reviews miss cross-file bugs by
  construction — each step can be correct alone while the set is broken where files meet (e.g. the
  credential store's secret values reaching the redaction filter only through `ConfigUI` construction
  order, the feed path reaching the supervisor's environment, the route set seen by the guard chain, the
  wizard's state file read by the landing logic, the dry run's run id matching the feed's). The review
  checks: every new route behind the guard chain in order; no token or credential value in any file,
  log, environment, feed or response (grep the test artefacts); the DD1 exemption is exactly three paths
  and no other `core/` file names a platform or vendor; `*.example` unchanged; dependencies unchanged;
  every allowlisted test adapted without loosening; the out-of-list edits (DD1 scans; any P16 ignore-rule
  change; DD7 outcome) listed for operator ratification. Write the review and the phase summary (what
  was built, the flagged decisions, and that the fifteen-minute claim awaits the operator trial record
  of P18).
- **Dependencies:** [P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14, P15, P16, P17, P18]
- **Tests:** The full suite (`pytest`) green with 0 new AC33 exemptions and 0 new process-spawning
  nodes; the AC5 referenced-key check and the AC20 reason coverage re-run over the final sources.
- **Risks:** A green suite does not prove D1's criterion (only the trial record does); the review must
  not approve while the record is unfilled if the gate requires it. Cross-step regressions surface here
  late — fixes go back to the owning step's files, each flagged in the review.

## Shared-file ordering

Every file edited by more than one step is edited along one dependency chain, so each step starts from
a defined baseline (each step lists its immediate predecessor in the chain among its Dependencies):

| File | Steps, in chain order (each depends on the previous) |
|---|---|
| core/config_ui/wizard.py | P4 → P5 → P10 → P12 |
| core/config_ui/__init__.py | P6 → P7 → P8 → P9 → P12 → P13 → P14 → P15 |
| core/config_ui/strings/en.json, fr.json | P3 → P10 → P12 → P13 → P14 → P15 |
| core/config_ui/console.py | P13 → P14 → P15 |
| core/main.py | P2 → P11 |
| tests/test_config_ui.py (incl. the cumulative `test_ac35` route set) | P6 → P8 (`/instance`) → P12 (`/wizard`, `/language`) → P13 (`/console`, `/feed`) → P14 (`/start`, `/stop`) → P15 (`/revoke`, final exact set) |
| tests/test_console.py | P2 → P13 → P14 → P15 |
| tests/test_first_run.py | P1 → P3 → P4 → P5 → P6 → P7 → P8 → P9 → P10 → P11 → P12 → P16 → P17 → P18 (each step appends its own test functions) |

## Caller enumeration

API changes: `UISettings.from_argv` / `_parser` (`--config` optional, `--install-launcher`), `UISettings`
(new derived paths), `startup_checks` (more problems), `core.config_ui.main` (keyword-only `opener`,
`serve`, instance seams), `ConfigUI.__init__` (merged environment, instance marker, wizard/console
collaborators), `ConfigUI.routes` (8 new entries), `Supervisor(...)` (environment carries stored
credentials and the feed path), `core.main.run` (feed variable), `core.main.dry_run` (new). Search:
`grep -rn "UISettings\.from_argv\|UISettings(\|Supervisor(\|config_ui\.main\|startup_checks(\|\.routes\b"`
over `core/ modules/ demo/ tests/` (4 sites in `core/config_ui/__init__.py`, 61 in
`tests/test_config_ui.py`, 1 each in `tests/test_input_ownership.py`, `tests/test_main.py`,
`tests/test_status_record.py`). Dynamic or reflective callers are not detected.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/config_ui/__init__.py | `main()` | P6/P8/P9: no `--config` → A2 default; opener once after checks; instance-record judgement before serving; `--install-launcher` exits; unchanged for `--config` apart from the opener call and the instance record |
| core/config_ui/__init__.py | `ConfigUI.__init__` → `Supervisor(default_launch_argv(settings), self.status_path)` | P7: pass `environ=` merged environment + feed path |
| core/config_ui/__init__.py | `startup_checks()` (called by `main`) | P6: extra collision problems only; existing messages byte-identical |
| core/config_ui/__main__.py | `main()` entry | No change (delegates) |
| pyproject.toml | console script `twitch-ia-compagnon-config-ui` | No change; valid with no argument |
| tests/test_config_ui.py | `UISettings.from_argv`, `Supervisor(...)`, `ConfigUI(...)`, `main([...])`, `ui.routes` | Existing calls keep passing `--config`/explicit `environ`; `test_a_missing_config_or_bad_port_exits_non_zero` (P6) and `test_ac35…` route set (P8, P12–P15) adapted per the allowlist; serving `main` calls hit the default opener, refused by the AC33 guard and swallowed (A3) |
| tests/test_input_ownership.py, tests/test_main.py, tests/test_status_record.py | `UISettings(...)` / `from_argv` / `core.main.main` | Keep passing `config=`; `core.main`'s CLI unchanged (`--config` stays required) |
| core/main.py | `run()` callers (`main`, tests across the runtime suites) | P2: behaviour identical without the feed variable (AC19) |
| demo/ | — | No caller of the changed APIs |

## Requirement coverage

| Requirement | Steps | Acceptance criteria |
|---|---|---|
| R1 | P1, P6 (landing routing), P8, P9, P12 (wizard landing), P13 (console landing), P15 (route set) | AC1, AC2, AC3, AC4, AC27, AC30, AC31 |
| R2 | P3, P10, P12, P13, P14 | AC5, AC6, AC32 |
| R3 | P4, P11 | AC7, AC8, AC9, AC10 |
| R4 | P5, P7, P12, P14 | AC8, AC11, AC12, AC32 |
| R5 | P4, P10, P11, P12 | AC9, AC13, AC14, AC15, AC16, AC17, AC28, AC29, AC30 |
| R6 | P2, P11 (feed run ids) | AC18, AC19, AC20 |
| R7 | P13, P14, P15 | AC20, AC21, AC22, AC23, AC32 |
| R8 | P16, P17, P18, P19 | AC24, AC25, AC26 |
| A1–A7, A3b | A1/A2 P1, P5, P6; A3 P6, P9; A3b P17, P18; A4 P4, P10, P11; A5 P10; A6 P15; A7 P5, P12 | — |

## Ordering rationale

Beyond functional needs, steps that edit the same file are chained explicitly (see "Shared-file
ordering"): P5 after P4 and P10 after P5 (`wizard.py`), P8 after P7, P9 after P8, P12 after P8/P9 and
P13 after P12 (`__init__.py`, the string tables and the cumulative `test_ac35` set), and the
`tests/test_first_run.py` chain. AC2 is split by what exists when: P6 asserts the landing *routing*
(redirect target and session cookie, and the editor page, which already exists); the wizard and console
pages behind those redirects are asserted end to end in P12 and P13, which create them.

P1 comes first because every later step that writes a platform-named byte under `core/` (strings,
profiles, the per-user directories) would otherwise turn three existing guards red; confining those
bytes and pinning the exemption before any of them exists keeps the suite green at every step. P2 (the
runtime feed) is independent of the UI and is needed by the supervisor environment (P7), the dry run's
feed correlation (P11) and the console (P13). The string table (P3) precedes every step that renders a
sentence; the profiles (P4) precede the validators (P10, which read endpoints and scopes from profile
data) and the dry run (P11, which drives the profile's fake event). The credential store and state file
(P5) precede the settings/landing work (P6, which routes on the state file and adds their collision
checks) and the environment merge (P7). The instance record (P8) needs the table for its reports and
P6's `main` seams; the launcher (P9) needs P6's argument parsing. The wizard screens (P12) assemble P5,
P6, P7, P10 and P11 and therefore come after all of them; the console (P13) needs the feed and the table,
Start/Stop (P14) builds on the console page, and Revoke with the final route-set assertion (P15) comes
last among the routes so the allowlisted exact-set test is finalised once every route exists (each
earlier route step extends it, so it never fails in between). Package data (P16) needs the data files of
P3/P4; the guide (P17) describes the finished screens and console; the trial template (P18) is
independent in content but is placed after P17 because both append to `tests/test_first_run.py`; the full-branch review (P19) is last because it inspects all changed
files together.
