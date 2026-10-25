---
name: "phase9-first-run"
version: "1.0"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec, phase9, first-run, wizard, console, profiles, i18n]
targets:
  - file: core/config_ui/__init__.py
    description: "`--config` becomes optional (absent → the per-user default profile location); first-run detection routes the access URL to the wizard; the access URL is handed to an injectable browser opener; a `--install-launcher` option delegates to the launcher writer and exits; the wizard, console, feed, start, stop, revoke and language routes join the route table behind the existing Host/session/method/Origin/CSRF guards (the GET-only `/instance` route is an authenticated route: Host- and method-checked like every other route, it requires the same access token, creates no session and answers only the instance marker and the product version); stored credentials join the secret set, the set/unset view and the environment of Check, Apply and the supervised runtime; the per-profile instance record (`<base>.instance.json`, published atomically, carrying an instance marker the `/instance` probe confirms, and the access token only where the file can be restricted to its owner) makes a second launch open the existing instance instead of serving twice, but only once the `/instance` probe authenticates with the recorded token and confirms the recorded marker (where no token is persisted no probe can authenticate: a live pid is reported as running but not answering and kept, and nothing is served); a confirmed instance of another product version is reported with both versions and nothing is opened; a record whose pid is not live, whose probe answers another marker, or that is incomplete is removed as stale before a fresh instance serves, and a record whose probe does not confirm within its total bound is reported and kept; the startup collision checks extend to the wizard state file, the credential file (`<profile-file-name>.credentials.json` in the per-user credential directory, A1) and the instance record (R1)."
  - file: core/config_ui/launcher.py
    description: "New: write (and report the path of) one per-user OS launcher that starts this interpreter's `-m core.config_ui`, so later launches need no terminal; target directories injectable for tests."
  - file: core/config_ui/wizard.py
    description: "New: the five wizard screens, their server-side step gating, the wizard state file (`<base>.wizard.json` beside the profile; its ownership record of the files it wrote, so an earlier correction may regenerate them, and its re-entry after a restart, A7), the Twitch / model / check / dry-run validators behind injectable transports, the plain-language failure mapping, the dry run's run record (R5), and the read-only rendering of the generated profile."
  - file: core/config_ui/console.py
    description: "New: the console page (feed, module health, start/stop with the raw-detail disclosure, grant list and revocation) and the mapping of every runtime reason token to a string-table sentence."
  - file: core/config_ui/strings/en.json
    description: "New: the English string table for the wizard, the console, the plain-language causes and next actions, and the four profiles' names and descriptions."
  - file: core/config_ui/strings/fr.json
    description: "New: the French string table, with exactly the key set of the English one."
  - file: core/config_ui/profiles/
    description: "New: the four ready-made profiles as data (chat Q&A, channel points, spoken keyword, silent) with their parameter slots and credential references."
  - file: core/main.py
    description: "When the feed path is supplied, publish the supervision facts (events arriving, runs started/ended, refusals with their reason token, module ready/degraded with cause, start/stop) to a bounded, redacted local feed; offer the dry-run entry the wizard uses (fake platform edges, nothing sent to a channel), which returns the dry run's run record (R5)."
  - file: core/overlay.py
    description: "Add the feed path's environment variable and its default (beside the status record), with the same collision refusal the status path has."
  - file: pyproject.toml
    description: "Add the `core.config_ui` package-data line shipping `strings/*.json` and `profiles/*`; runtime dependencies stay exactly aiohttp and PyYAML."
  - file: docs/first-run.md
    description: "New guide: the first fifteen minutes for a non-technical streamer (the install path and launcher, start the UI from the launcher, the five wizard screens, the console, where the profile and the credential file live)."
  - file: docs/first-run.fr.md
    description: "New: the French translation of the first-run guide, with the same shape, commands and identifiers."
  - file: docs/README.md
    description: "Link the first-run guide pair from the documentation index."
  - file: docs/campaigns/phase9/trial-record.md
    description: "New: the operator trial record template and, after the trial, its filled record (who, machine, profile, how the install was done and how long it took, trial start, timestamps per screen, first run, elapsed minutes, what was actually done, outcome, limits)."
  - file: tests/test_first_run.py
    description: "New: first-run detection, default location, the launcher writer, string tables, the four profiles (check-config and dry run), credential store, wizard gating, regeneration of the wizard's own files, re-entry after a restart, the second launch, the dry run's run record and every validator failure class, all against fakes."
  - file: tests/test_console.py
    description: "New: feed bounds and redaction, plain-language reasons coverage, start/stop through a fake supervisor child, the cause/raw-detail split, revocation, and the guard order on every new route."
  - file: tests/test_config_ui.py
    description: "Adapt the two allowlisted assertions (route set; `main([])` no longer an error by itself) without loosening either."
  - file: tests/test_profiles.py
    description: "Adapt the existing installed-distribution node (an A2 node) to also assert that the install target holds the 2 string files and the 4 profiles byte-identical to the checkout (AC26); its 21-manifest count is unchanged, no node is added or exempted."
  - file: tests/test_docs_bilingual.py
    description: "Add `first-run` to the declared guide pairs (allowlisted)."
---

# Phase 9 — the first fifteen minutes: wizard, console, ready-made profiles

READ: docs/campaigns/phase9/brief.md

## Problem

Everything the product can do is reachable only by someone who already knows how it is built. The
configuration UI (`core/config_ui`, `python -m core.config_ui --config PATH`) is a precise form editor
over the YAML: field by field, JSON textareas for lists, CSRF, secret redaction and a *Check* button. It
assumes the reader knows the schema, the module names and what a trigger policy is. Every credential is an
environment reference (`${NAME}`) that a non-technical streamer has no way to set without a terminal. The
UI only answers "is the process up" (the status record: pid, digest, module `ready`/`degraded` with no
cause); nothing on any page answers *« est-ce qu'il m'a compris, et est-ce que ça marche ? »*.

This phase makes the existing product reachable by a streamer on their own PC, without a terminal and
without YAML, inside the existing local web application: a five-screen wizard whose every step is
validated by an observed success, exactly four ready-made profiles, and a console that shows what the
runtime is doing and why it refused something, with start/stop and grant revocation. It adds no
capability, module or trigger, no packaging, no dependency and no socket in tests.

### Binding decisions carried from the brief (operator, 2026-10-22)

D1 audience and criterion (operator trial under fifteen minutes, recorded, not measured by a test),
with **Amendment A1**: the criterion starts once the package is installed — from a working install to a
first run in under fifteen minutes, without a terminal and without YAML — and the trial record states how
the install was done and how long it took (Phase 10 owns the terminal-free installation);
D2 one surface (`core/config_ui`, same binding/token/CSRF/origin/redaction rules, no second server or
token, never a secret value rendered); D3 every wizard step validated by an observed success, failures
in plain language with the next action; D4 exactly four profiles; D5 a console over the same supervisor;
D6 YAML hidden, viewable read-only, edited only in the existing editor; D7 English and French from one
string table, a third language is data; D8 no new dependency, no new socket in tests.

### Assumptions (documented, the brief is silent on them)

- A1 — **Credentials are stored locally.** A streamer without a terminal cannot set environment
  variables, so the values the wizard validates are kept in one credential file in a per-user
  application data directory, readable and writable by the owner only. It is no new kind of file and holds no new kind of
  content: it is the environment the product already reads, kept in a file under the same rules. Each
  entry is keyed by one of the `${NAME}` references the operator's configuration (the profile) lists
  under `secrets`, and holds exactly what the environment variable of that name would hold — the real
  value, where the product already requires one, as the wizard validated it; it is never a resolved
  profile (no profile content with its references substituted) and never holds anything but those named
  entries. **One credential file per profile, in a per-user directory on every platform.** The
  directory is the per-user application data directory: on Linux and macOS the directory the product
  already uses for its per-user configuration (A2's: `$XDG_CONFIG_HOME/twitch-ia-compagnon/`, falling
  back to `~/.config/twitch-ia-compagnon/`; `~/Library/Application Support/twitch-ia-compagnon/`),
  created owner-only (0700) if absent, the file written with mode 0600 from its creation; on Windows
  `%LOCALAPPDATA%\twitch-ia-compagnon\`, whose per-user access the operating system already restricts to
  the account. The product does not rewrite or inspect ACLs and claims no more than the platform gives;
  screen 4 says, in plain language (string table), which directory it used and why. The file is named
  from the profile's **file name** (its full name, suffix included) and documented:
  `<profile-file-name>.credentials.json` (`profile.yaml.credentials.json` at the A2 default; the guide
  names it, AC24), so two profiles in one directory — `a.yaml` and `a.yml` included — never map to one
  file; it is the file R1's startup collision set, and AC1, name by that path. Its name has the form of
  its siblings' (`<base>.wizard.json`, `<base>.instance.json`) but not their place, on purpose: the state
  file and the instance record carry no secret and belong beside the profile in its directory, while the
  credential file belongs to one profile yet lives in the one per-user directory every profile shares,
  because that is where the platform restricts access. Since profiles in different directories may share
  a file name, the file is read, used and replaced only for the profile whose wizard state file (A7)
  records its digest; for any other profile it is neither read nor written, and screen 4 refuses with a
  plain-language cause naming the file by its path. **The only refusal.** Where the platform gives no
  per-user restriction — on Linux or macOS a directory not owned by the user or writable by group or
  others, on Windows `%LOCALAPPDATA%` unset or not an existing directory — the wizard writes no
  credential file and no collected value anywhere, and screen 4 says so (string table) and stays closed.
  No environment variable is ever required of the operator, and no screen depends on a relaunch to see
  one. The profile itself holds only `${NAME}` references
  and lists them under `secrets`. The UI supplies the stored values as environment to the check, the dry
  run and the runtime it launches; for a name the store holds, the stored value is the one used (what the
  wizard validated is what runs). A runtime started from a terminal does not read the file; the guide says
  so.
- A2 — **Default location.** Started without `--config`, the UI uses one per-user profile path
  (`$XDG_CONFIG_HOME/twitch-ia-compagnon/profile.yaml`, falling back to `~/.config/…` on Linux;
  `%APPDATA%\twitch-ia-compagnon\profile.yaml` on Windows; `~/Library/Application Support/
  twitch-ia-compagnon/profile.yaml` on macOS). "First run" means: the base profile file does not exist.
  With `--config` given, every existing behavior is unchanged except that a non-existent path is also a
  first run.
- A3 — **Opening the page without a terminal.** The UI hands its access URL (which carries the token) to
  the system's default browser once at startup, through an injectable opener; it still prints it once and
  never logs it. Repeat launches go through a per-user OS launcher (R1) that the streamer clicks like any
  application: a `.desktop` entry in the user's applications directory on Linux, a `.cmd` shortcut in the
  user's Start Menu programs folder on Windows, a `.command` file in `~/Applications` on macOS, each
  running the interpreter the package is installed in with `-m core.config_ui`.
- A3b — **The install precedes the criterion (brief Amendment A1).** Getting Python and the package onto
  the machine and writing the launcher (`--install-launcher`) make up the install; they follow the one
  install path the guide documents and may be performed by someone other than the streamer (e.g. the
  operator). The criterion starts once the package is installed: from a working install to a first run in
  under fifteen minutes, without a terminal and without YAML. The trial clock therefore starts at the first
  launch of the installed product (the click on the launcher) and stops at the first recorded run; the
  "terminal/YAML touched" field covers that interval. The trial record states how the install was done
  (who performed it, by which means, and whether a terminal was used) and how long it took, and lists the
  install under remaining limits, so the gap Phase 10 closes is visible in the record.
- A4 — **The silent profile needs no model.** "Everything off, the watchdog profile": the platform
  input is connected and every trigger is off, so the model step is shown as not needed for it and no
  model request is ever made.
- A5 — **Channel points by title, stored by id.** The phase-8 `reward` trigger matches `reward.id`. The
  wizard lists the channel's custom rewards (one bounded read after the token is validated) and the
  streamer picks one by title; the profile stores its id.
- A6 — **Revocation removes a grant through the managed overlay** (the base profile is never rewritten by
  the console). If a runtime this UI supervises is running, the removal takes effect through the existing
  supervised restart (Apply) and its report is shown; otherwise at the next start. The console cannot add
  or widen a grant.
- A7 — **The wizard is a first-run surface; it owns only what it wrote, and it is finished only by
  screen 5.** The wizard starts when the access URL lands on it at first run. Its progress lives in a
  wizard state file beside the profile — the base file's full name with `.wizard.json` appended, as the
  status record's default is formed — (owner read/write only on POSIX, replaced atomically, holding no
  secret value; its startup collision check is R1's): the profile choice and its non-secret parameters, the language, the digest of each file
  screen 4 wrote, and `completed` once screen 5 succeeds. The profile and credential file written by
  screen 4 are the wizard's own: correcting an earlier input invalidates screen 4 (R5), and passing
  screen 4 again replaces those files, but only while each is byte-identical to the digest the state
  recorded at write time. A file the wizard did not write, or one changed since it wrote it, is never
  replaced.
  **Re-entry.** A new UI process over the same profile directory reads the state file. *Unfinished*
  (state present, not `completed`): the access URL lands on the wizard again, whatever screen was
  reached before, including after screen 4 wrote the profile. A restart resumes the inputs, never a
  success: screen 1 comes back passed with its recorded inputs, credentials show as "kept", and every
  screen from 2 on must be passed again in the new process — the Twitch credential and the model endpoint
  are re-validated (a token valid days ago may have expired), screen 4's check is re-run, and screen 5's
  dry run is never skipped. If the profile or credential file is no longer byte-identical to what the
  wizard wrote, screen 4 refuses to replace it (cause: the file was changed outside the wizard; next
  action: the editor) and the wizard stays unfinished. *Completed*: the access URL lands on the console
  (default location) or on the editor (`--config`, unchanged) and the wizard is never offered again;
  later changes go through the existing editor, linked from the console. *No state file and a profile
  present* (a profile that predates this phase or was written by hand): console or editor, as for
  completed. A failed screen never ends or completes the wizard: it can be submitted again as many times
  as needed, with its inputs and kept credentials preserved.

## Requirements

- R1: **First run is detected and reached without a terminal.** `python -m core.config_ui` (and the
  existing `twitch-ia-compagnon-config-ui` console script) starts with no argument, using the A2 default
  location and creating its parent directory owner-only if absent. The existing startup checks (port,
  loopback, path canonicalisation, overlay/status collisions) apply unchanged to the default paths, and
  extend to the three files this phase adds beside the profile: the wizard state file (A7,
  `<base>.wizard.json`), the credential file (A1, `<profile-file-name>.credentials.json` in the per-user credential directory, named from the profile's full file name so that `a.yaml` and `a.yml` never share one) and the
  instance record (below, `<base>.instance.json`). None may resolve (canonical form, same file) to the
  base profile, the overlay, the status record, the feed path or another of the three; a collision
  refuses to start (exit non-zero, nothing served) with a value-free diagnostic that names the colliding
  file's role and path and the file it collides with, never any file's content (the credential file is
  named by its path only). When
  the base profile does not exist, or the wizard state file says the wizard is unfinished (A7), the
  access URL's landing is the wizard; otherwise it is the console (default location) or the editor
  (`--config` given, as today). The access URL is handed once to an
  injectable browser opener (A3); a failing or absent browser leaves the printed URL as the fallback and
  never stops the UI. `--install-launcher` writes the A3 launcher for the running platform (owner-writable,
  pointing at the running interpreter and module, carrying no token, no secret and no `--config` unless
  one was given), prints its path and exits without serving; an existing launcher at that path is
  replaced only if it is one this option wrote (recognisable marker line), otherwise refused. **A second launch never serves twice and never does nothing silently.** A serving
  UI holds an instance record beside the profile (`<base>.instance.json`) naming its pid, its port, an
  instance marker (128 random bits, distinct from the access token, also held in the serving process's
  memory) and, only where the file is restricted to its owner, its access URL. **The token is persisted
  only where the specification can guarantee owner-only access.** On POSIX the record's file has mode
  0600 from its creation and the record carries the access URL. For a file beside the profile the
  specification guarantees owner-only access through that mode only, so on every other platform (Windows
  included) the record is a file that cannot be restricted to its owner: there the record carries no access URL and the access token is not
  persisted at all, in this file or any other. The token is never written with permissions the
  specification cannot guarantee. The record is written atomically: its content goes to a temporary file in
  the same directory, mode 0600 on POSIX from its creation, which is then published under the
  record's name by a no-replace rename (POSIX: a hard link to the record's name, which fails if that name
  exists, then removal of the temporary file; Windows: a rename, which fails if the target exists). A
  reader therefore sees a complete record or none; a record that does not parse, lacks one of the fields
  its platform requires (POSIX: pid, port, access URL, marker; elsewhere: pid, port, marker) or holds a
  field of the wrong type is incomplete. Atomic publication means a serving UI only ever publishes a
  complete record, so an incomplete one can only be what a crash left behind: it is stale, removed as
  every stale record is (below), never taken as a serving UI and never a crash of this launch. **Judging a complete record.** Its pid is
  first tested for liveness (the test the status record already uses); a pid not live makes it stale.
  *Live pid, POSIX:* the launch probes `/instance` (below) on the recorded port's loopback address with the
  recorded access token. Each attempt is injectable and bounded to 2 s; a refused connection, a timeout or
  a non-200 answer fails that attempt only, and the probe is retried every 0.5 s until an attempt
  answers or 10 s in total have elapsed since the first. An answer carrying the recorded marker
  confirms the serving UI; an answer carrying another marker makes the record stale. A slow UI is not a
  dead one: when no attempt answers within the 10 s, the record is **not** removed (it is left
  byte-identical), no server starts, the opener is not called and no access URL is printed; the launch
  prints a plain-language report of the two observed facts — an instance record exists for this profile
  (its path), and what its recorded port did (answering but the probe did not authenticate; or not
  answering within 10 s) — with the cause and the next action from the string table (use the companion
  window if one is open; otherwise wait and launch again, and if the report repeats, close the program
  holding the recorded pid or restart the computer), and exits non-zero. **A record counts only when the
  probe authenticates with the token.** A live pid alone never confirms a serving UI: a pid may have been
  reused by another program, and the liveness test cannot tell. On POSIX the marker closes that gap — an
  authenticated answer carrying the recorded marker is this UI, one carrying another marker is not.
  *Live pid, elsewhere:* the platform gives the launch no way to inspect a pid's identity, and no token is
  persisted (above), so no probe can authenticate and none is made; the marker, which only an
  authenticated probe can read, cannot be confirmed. This is a stated limitation: the record is neither
  confirmed nor judged stale, it is reported as running but not answering and kept — left
  byte-identical, no server starts, the opener is not called — with the observed facts (an instance record
  exists for this profile, its path, its pid is live, and this platform cannot confirm that it is the
  companion) and the next action from the string table (use the companion window if one is open, on
  `127.0.0.1` at the recorded port; otherwise close the program holding the recorded pid or restart the
  computer), and the launch exits non-zero. A launch that finds a confirmed serving UI starts no server.
  **Same version.** When the version the `/instance` answer carries equals this launch's own product
  version (its installed distribution's version), the launch hands the recorded access URL — on the
  recorded port, even when that differs from the port this launch asked for — to the opener, prints a
  plain-language "already running" line with that URL, and exits 0; a failing opener leaves that printed
  line as the fallback. **Another version.** When the versions differ, the launch never opens an
  instance of a different version: the opener is not called, nothing is served, the record is kept
  byte-identical, and it prints a plain-language report naming both versions (the running one and its
  own) with the next action from the string table — close the running companion window and launch again,
  or open the running one manually at the URL the report prints — and exits non-zero. Only these records
  are stale: an incomplete
  one, one whose pid is not live, and (POSIX) one whose probe answers another marker, as when the pid was
  reused: the launch reports, value-free, that an earlier
  instance did not stop cleanly, removes the record — renaming it to a launch-unique name in the same
  directory and deleting that only if its bytes equal the ones it judged stale, otherwise restoring it
  under the record's name with a no-replace rename and re-reading — and a fresh instance publishes its
  own record and serves normally. A launch that loses the no-replace publication to a concurrent one
  re-reads the record and behaves as a second launch. **The access token the record carries** (inside
  its access URL, POSIX only) is never placed in a runtime status record, the feed or the supervised
  runtime's environment, never written to a log record or a diagnostic, and never rendered in a page or
  response (`/instance` answers the marker and the product version only); a clean exit removes the record
  (only while it still holds this instance's marker). The POSIX "already running" line prints
  the URL on standard output as A3's single print does; a print is not a log record. Every
  new page and endpoint sits behind the existing guards in their existing order
  (Host, session, method, Origin, CSRF on every state change), with the same single token and server.
  **`/instance` is an authenticated route, not a session exception.** It requires the same access token
  the access URL carries, presented as the access URL presents it and compared in constant time, and
  refuses a missing or wrong token as the access URL does; it is Host-checked and method-checked like
  every other route (Host, method, token, in that order); it answers GET only, changes no state, creates
  no session, and answers only the serving process's instance marker and the product version (the
  installed distribution's version). It is one of the new routes the route-set criterion lists (AC23 and
  the route-set allowlist entry), so its authentication is asserted with theirs (AC4).

- R2: **One string table, two languages, a third by data.** Every user-visible sentence of the wizard,
  the console, the plain-language causes and next actions, and the four profiles' names and descriptions
  comes from a string table with one data file per language. English and French ship with identical key
  sets and non-empty values. The language is a setting chosen on the wizard's first screen and on the
  console, persisted beside the profile, defaulting to English. A language data file placed beside the
  shipped ones with the complete key set becomes selectable with no code change; an incomplete one is not
  offered. Placeholders in a sentence (e.g. a field name, a channel name) are the same set in every
  language. The existing editor's text is not moved into the table and is unchanged. Raw tool output shown behind
  the R7 disclosure is quoted data, not a sentence of the product: it is not translated and needs no key,
  and its disclosure label comes from the table.

- R3: **Exactly four ready-made profiles, each complete and proven.** The choice screen offers exactly
  four profiles, shipped as package data: **chat Q&A** (answers a chat message that mentions the
  companion name), **channel points** (reacts to one named custom reward's redemption), **spoken keyword**
  (the wake word on `audio_input`, phase 8 R7), and **silent** (platform input connected, every trigger
  off). Each declares the parameters the streamer supplies (companion name; reward; wake word, detector and
  transcription settings; none) and the credential references it needs. A profile generated from any of
  them with valid parameters: references every credential only as `${NAME}` and lists each such
  reference under `secrets`; contains no literal credential; passes `--check-config` with exit 0; and its
  dry run behaves as R5 states. No profile enables a module, action grant or trigger the shipped runtime
  does not already provide. The shipped `*.example` profiles are unchanged.

- R4: **Credentials are kept, used and never shown.** The values the wizard collects are written only
  to the A1 credential file in the per-user credential directory (mode 0600 on POSIX, the OS-restricted
  `%LOCALAPPDATA%` on Windows; replaced atomically; never inside the profile or the overlay); where A1
  refuses, it is not written and no collected value is written anywhere. Stored values join the UI's secret set, so every response, log line,
  report, stderr tail, feed entry and read-only profile view is redacted against them; the editor's
  set/unset judgement counts a stored value as set. The UI supplies the stored values as environment
  (stored value wins for the names it holds) to the configuration check, the dry run, Check, Apply and the
  runtime its supervisor launches. No page, form value (including re-rendered forms after a failure),
  redirect, or link ever carries a stored or submitted secret value; a credential field is re-rendered
  empty with a "kept" state.

- R5: **Five wizard screens, each gated by an observed success.** The screens, in order: (1) language and
  profile choice with that profile's parameters; (2) Twitch: the submitted credential is accepted by
  Twitch's token validation, its client id matches, it carries the scopes the chosen profile needs, and
  the channel it names is shown; the broadcaster and bot identities are taken from the validation answer,
  not typed; for channel points the channel's custom rewards are read and one is picked (A5); (3) model:
  one real completion request to the submitted endpoint and model is answered with non-empty text
  (skipped as "not needed" for the silent profile, A4); (4) profile: the generated profile is always written, and the credential file
  is written where A1 allows it (R4) and never elsewhere — where A1 refuses, screen 4 says so and stays
  closed (both never over a file the wizard did not write, A7; a file it did write and
  that is unchanged since is replaced when screen 4 is passed again), it passes `--check-config`, and its YAML can be
  viewed read-only on request; (5) dry run: one fake event of the profile's kind is driven through the
  generated profile with fake platform edges, nothing is sent to any channel, and the dry run produces
  one **recorded run**: a run record returned by the dry-run entry, carrying a run id, an outcome and its
  origin as this dry run (origin `dry_run`, naming the fake event the dry run drove). For a profile with a
  trigger, the run id is that of the run the admission scheduler recorded for the fake event and the
  outcome is that run's completed status; for the silent profile (every trigger off, A4) the outcome is
  "not triggered" with the trigger's reason token and 0 model requests. The record's outcome is shown.
  **A screen 5 whose dry run does not produce a recorded run cannot be completed** — no record (the dry
  run timed out, exited, or published events without returning one), a record without a run id, an
  outcome or the `dry_run` origin, or a record whose outcome is not the profile's expected one — and only
  a completed screen 5 marks the wizard `completed` (A7).
  *Next* is enforced server-side: a request for screen n+1 is refused unless screen n's success is
  recorded for the current inputs, and changing an input of a validated screen invalidates that screen and
  every later one. Each validator is bounded (Twitch 10 s, model 30 s, dry run 60 s). A failure shows one
  plain-language cause and the exact next action from the string table, never a stack trace, exception
  text or submitted value; the failed screen stays open for another attempt (A7). The wizard never starts
  the runtime; its last screen links to the console.

- R6: **The runtime publishes a bounded, redacted feed the console reads.** When the feed path is
  supplied (the UI's supervisor always supplies it, beside the status record, with the same collision
  refusal), the runtime appends one entry per supervision fact: platform event arrived (kind, platform,
  author display name, text excerpt), trigger accepted/rejected with its reason token, admission
  accepted/rejected with its reason token, run started, run completed with its status and reason token,
  action refused with its reason token, module ready/degraded/stopped with the degraded cause, runtime
  started and stopping. The feed retains at most the latest 200 entries; an entry is at most 1 024 bytes
  and any excerpt at most 80 characters; every entry is redacted against the runtime's `secrets` before
  it is written; a write failure never delays or fails publication on the bus. Without the feed path the
  runtime behaves exactly as before.

- R7: **The console answers « est-ce que ça marche ? » through the same supervisor.** The console page
  shows the runtime state (running under this UI / not running / another process not supervised), each
  enabled module with ready or degraded and, for degraded, its cause and a next action, and the feed's
  entries newest first, refreshed by polling a token-gated endpoint over the same server. Every reason
  token the runtime can emit (trigger, admission, run, action and authorization reason constants) is
  rendered as a plain-language sentence in the chosen language; an unknown token renders a generic
  sentence naming the token, never an empty line. **Start** launches the profile through the existing
  supervisor only when no live process holds the status record; **Stop** stops only the child this UI
  launched; both are CSRF-guarded POSTs and report their outcome in plain language. A runtime that exits during
  startup shows two distinct surfaces: the **cause** — a plain-language sentence and the next action from
  the string table, in the chosen language, always shown — and the **raw detail** — the existing bounded
  stderr tail, untranslated, available only behind an explicit disclosure control that is closed by
  default, and redacted by the R4 rules like every other surface. The cause never quotes the raw detail,
  and the raw detail never replaces the cause. The console lists the configured
  grants (rule id, action, principal, destination) and offers **Revoke** per grant (A6); it offers no
  control that adds, widens or bypasses a grant, a policy, an admission bound or a module setting.

- R8: **Documented, trialled, and still dependency- and socket-free.** `docs/first-run.md` and
  `docs/first-run.fr.md` are a declared bilingual guide pair describing the default location, the
  credential file (its per-user directory on each of the 3 platforms, A1), the five screens, the console
  and how an operator moves on to the editor. The guide also documents the install path and
  `--install-launcher` (A3b). The trial record states, for a real non-technical person on their own PC:
  date, commit, machine and platform (Linux, macOS or Windows, with its version), how the install was done (who performed it, by which means, and whether a
  terminal was used, A3b), how long the install took, profile chosen, the trial start time (the first
  launch of the installed product: the click on the launcher), the time at which each of the five screens
  was passed, the time of the first recorded run, the elapsed minutes from trial start to first recorded
  run, whether the streamer touched a terminal or YAML at any point between those two times, the observed
  outcome and the remaining limits (including the install step); the phase's fifteen-minute claim — from a working install to a first run in under fifteen minutes,
  without a terminal and without YAML (Amendment A1) — rests on that record only, and holds only when the
  elapsed time is under 15 minutes, and only for the platform the record names (a first run on one
  platform is never claimed from an observation on another). Runtime dependencies stay exactly `aiohttp` and
  `PyYAML`; the strings and profiles ship as package data; the suite opens no network socket and starts no
  process outside the existing named exemptions (the Twitch and model validators, the browser opener, the
  instance probe, the dry run and the supervisor run against fakes).

## Acceptance criteria

- AC1 (R1): `UISettings.from_argv([])` resolves the base path to the A2 default for the running platform
  (with `XDG_CONFIG_HOME=<tmp>`: `<tmp>/twitch-ia-compagnon/profile.yaml`); `startup_checks` on it returns
  0 problems; with `--host 0.0.0.0` and no `--allow-non-loopback` it still returns the existing refusal.
  With `XDG_CONFIG_HOME` unset and `HOME=<tmp>` the default is `<tmp>/.config/twitch-ia-compagnon/
  profile.yaml`. With the status path, and in a second case the overlay path, configured (as the existing
  collision tests configure them) to `<base>.wizard.json`, and in two further cases to
  `<base>.instance.json`, and in two further cases to `<profile-file-name>.credentials.json` in the per-user credential directory (`<tmp>/twitch-ia-compagnon/profile.yaml.credentials.json`), each
  with a fixture content string written to that file, `startup_checks`
  returns exactly 1 problem naming the colliding file's role and path and the file it collides with,
  containing 0 occurrences of the fixture content, and `main([], serve=<fake>)` exits non-zero with
  `serve` called 0 times.

- AC2 (R1): With the base profile absent, a GET of the access URL (`/?token=…`) with an accepted Host
  answers with a session cookie and lands on the wizard's first screen; with a profile present and no
  `--config`, it lands on the console; with `--config <existing file>`, it lands on the existing editor
  page whose markup is byte-identical to the pre-phase render for the same fixture except for at most one
  added link to the console.

- AC3 (R1): `main([], opener=<fake>, serve=<fake>)` calls the opener exactly 1 time with a URL equal to
  `ui.access_url`, prints that URL exactly 1 time, and logs 0 records containing the token; an opener
  that raises leaves the exit status 0 and the URL printed.

- AC4 (R1): For each new route (for `/instance`: (a), (c), and in place of (b) a missing or wrong access
  token, refused with the status code the access URL uses for a wrong token, with 0 sessions created and
  0 marker bytes in the answer; and a GET with an accepted Host and the access token answers 200 with
  exactly the instance marker and the product version and creates 0 sessions), requests with (a) a wrong Host, (b) no session, (c) a wrong method,
  (d) a foreign Origin on a state change, (e) a missing or wrong CSRF token on a state change are refused
  with the same status codes the existing routes use for (a)–(e), and 0 validator, supervisor, file-write
  or feed-read calls are recorded on the fakes.

- AC5 (R2): The English and French string files parse, have equal key sets of at least 1 key each, every
  value is a non-empty string, and for every key the set of `{placeholder}` names is identical in both.
  Every string key referenced by `core/config_ui/wizard.py` and `core/config_ui/console.py` exists in
  both files (0 missing keys).

- AC6 (R2): Copying `en.json` to a fixture strings directory as `de.json` makes `de` appear in the
  language choice with 0 source files changed; deleting 1 key from that copy removes `de` from the choice.
  Choosing `fr` on the first screen renders the next page with the French sentences of its keys, and the
  choice survives a new `ConfigUI` instance over the same profile directory.

- AC7 (R3): The choice screen renders exactly 4 profile options, in the order chat Q&A, channel points,
  spoken keyword, silent, each with its name and description from the string table in both languages.

- AC8 (R3, R4): For each of the 4 profiles, generated with fixture parameters into a temporary
  directory: the profile file contains 0 of the fixture credential values; every `${NAME}` reference it
  contains appears under `secrets`; and `check_config` with the stored credentials supplied as environment
  returns exit 0 with 0 diagnostics and 0 sockets opened.

- AC9 (R3, R5): Dry runs over the generated profiles with a fake model answering `"bonjour"`:
  chat Q&A with a fake chat message mentioning the companion name → exactly 1 run started and 1 run
  completed with a success status, the displayed answer `bonjour`, 0 messages sent to the fake platform;
  chat Q&A with a message not mentioning the name → 0 runs and 1 trigger rejection with its reason;
  channel points with a fake redemption of the picked reward id → 1 run, and with another reward id → 0
  runs; spoken keyword with a fake speech event carrying the wake word → 1 run; silent with a fake chat
  message → 0 runs, 1 rejection with its reason, 0 model requests, and the screen reports success on its
  run record (AC29).

- AC10 (R3): The 4 shipped `*.example` profiles are byte-identical to `cb0f5b1`, and each generated
  profile's `enabled_modules` is a subset of the 21 shipped manifests' names, with 0 action names, trigger
  types or event kinds not already declared by a shipped manifest.

- AC11 (R4): After screen 2 succeeds with fixture token `tok-XYZ…` and screen 4 writes the files, the
  credential file `<profile-file-name>.credentials.json` exists in the per-user credential directory with
  mode `0o600` (on POSIX), holds the value under its reference name and no profile content, and no other
  profile's credential file in that directory is touched (profiles `a.yaml` and `a.yml` get 2 distinct
  files); with the platform forced to Windows and `LOCALAPPDATA=<tmp>`, the file is under
  `<tmp>/twitch-ia-compagnon/` and screen 4 names that directory and why; with the per-user directory
  made group-writable (POSIX) or `LOCALAPPDATA` unset (Windows), no credential file exists, no file in
  the profile's or the per-user directory holds the value, and screen 4 states the refusal and keeps
  *next* closed; a credential file of that name whose digest the profile's state file does not record is
  left byte-identical and is not read; and the
  profile, the overlay and every response body of the session (including the read-only YAML view and the
  failure re-render of screen 2 with a deliberately wrong client id) contain 0 occurrences of the value or
  its URL-encoded, HTML-escaped and JSON-escaped forms; the credential input renders with an empty value
  and a "kept" state.

- AC12 (R4): With the stored credentials holding `TWITCH_ACCESS_TOKEN=stored` and the UI's own
  environment holding `TWITCH_ACCESS_TOKEN=inherited`, the environment passed to the fake supervisor's
  `popen`, to the configuration check and to the dry run each contains `TWITCH_ACCESS_TOKEN=stored`; the
  editor shows that credential as set. A log record and an Apply stderr tail containing `stored` are
  rendered with it redacted.

- AC13 (R5): A POST to advance to screen 3 before screen 2's success is refused, re-renders screen 2, and
  records 0 model requests; after screen 2 succeeds, changing the submitted client id invalidates screens
  2–5 (a request for screen 3 is refused again). The same holds for every pair n → n+1 with n in 1..4.

- AC14 (R5): Against a fake Twitch validation endpoint, each of these outcomes yields a distinct
  string-table cause with a next action and keeps *next* closed: connection refused; no answer within
  10 s (fake clock); HTTP 401; a client id differing from the submitted one; a missing scope required by
  the chosen profile (the missing scope names are shown); a malformed answer. A 200 answer naming login
  `streamer_x` and user id `123` shows `streamer_x` and fills the broadcaster and bot identities with
  `123` in the generated profile. For channel points, a fake reward list of 2 rewards renders 2 choices
  by title; an empty list yields its own cause ("create the reward first") and keeps *next* closed.

- AC15 (R5): Against a fake OpenAI-compatible endpoint, each of these outcomes yields a distinct
  string-table cause with a next action and keeps *next* closed: connection refused; no answer within
  30 s (fake clock); HTTP 401/403; HTTP 404; a body that is not a chat-completion shape; an empty answer
  text. An answer with non-empty text opens *next* after exactly 1 request. For the silent profile the
  screen is marked not needed and 0 requests are made.

- AC16 (R5): Screen 4 refuses to write when a profile file appeared at the target path after first-run
  detection (the existing file is byte-identical afterwards); when the generated profile's check returns
  exit 2 (fixture: a malformed detector setting for spoken keyword), the screen names the offending field
  path in plain language and keeps *next* closed; the read-only view is a non-editable rendering with 0
  form controls that post profile content, and 0 of the routes this phase adds (wizard, console, feed,
  start, stop, revoke, language) accept YAML content; the existing editor's routes are unchanged (AC2).

- AC17 (R5): Every failure response of AC14–AC16 and of a dry run exceeding 60 s (fake clock) contains 0
  occurrences of `Traceback`, `File "`, an exception class name, or any submitted value; the wizard
  records 0 supervisor `start` calls over a complete successful run through screens 1–5.

- AC18 (R6): With the feed path supplied and a fake runtime publishing 250 supervision facts, the feed
  holds exactly the latest 200 entries in publication order; an entry built from a 5 000-character chat
  message is at most 1 024 bytes with an excerpt of at most 80 characters; an event whose text contains a
  value listed under `secrets` is written with that value redacted; a feed write that raises leaves the
  bus publication completing and every handler invoked.

- AC19 (R6): Without the feed path, a full fake-edged run produces 0 feed files and the same bus history
  and status records as before the phase (the existing runtime suites pass unmodified). A feed path that
  resolves to the base, overlay or status file is refused at startup with a collision diagnostic naming
  which.

- AC20 (R6, R7): Every reason constant exported by `core/triggers.py`, `core/admission.py` and
  `core/actions.py`, and every reason token emitted on `input.trigger.rejected`, `brain.admission.rejected`,
  `brain.run.completed` and refused actions by the shipped modules (collected by the test from the source),
  has a string-table sentence in both languages (0 missing); a fixture entry with reason `zz_unknown`
  renders a non-empty generic sentence containing `zz_unknown`.

- AC21 (R7): With a fixture feed containing 1 arrived event, 1 accepted run (started + completed),
  1 rejection with reason `self_authored` and 1 `module.degraded` for `audio_output` with a cause, the
  console renders exactly 5 items (one per feed entry: arrived, run started, run completed, rejection,
  degraded) newest first, i.e. the degraded entry first and the arrived entry last, the rejection as its French sentence when `fr` is chosen, and the
  degraded module with its cause and a next action; the poll endpoint called with the cursor of the
  newest entry returns 0 entries.

- AC22 (R7): With a fake supervisor: Start with no live status record calls `start` exactly 1 time with
  the profile's launch argv and the feed path in the environment; Start while a usable status record names
  a live pid not launched by this UI calls `start` 0 times and shows "another instance is running";
  Stop calls `stop` exactly 1 time on the child this UI launched and 0 times otherwise; a child that exits
  1 during startup yields the two surfaces of AC32.

- AC23 (R7): With a profile granting rules `brain-reads-chat` and `brain-reads-users`, the console lists
  both; Revoke on `brain-reads-users` leaves the base profile byte-identical, writes the managed overlay so
  that the merged `actions` lists only `brain-reads-chat`, and, with a supervised child running, performs
  exactly 1 supervised restart and shows its report; afterwards a `users.read` call is refused with
  `no_applicable_rule` and the console renders that refusal's sentence. The console exposes 0 routes or
  controls that add a grant or change a policy, admission bound or module setting (the route table's new
  entries are exactly the wizard, console, feed, start, stop, revoke, language and `/instance` routes,
  `/instance` being token-authenticated as AC4 asserts, not an unauthenticated route).

- AC24 (R8): `docs/first-run.md` and `docs/first-run.fr.md` exist, are discovered as a pair by
  `tests/test_docs_bilingual.py` with `first-run` declared, pass its shape and identifier checks, and are
  linked from `docs/README.md`; the guide names the default location of all 3 platforms and the
  credential file by its name, `<profile-file-name>.credentials.json` (`profile.yaml.credentials.json`
  at the default location), and its per-user directory on each of the 3 platforms (`%LOCALAPPDATA%` on
  Windows).

- AC25 (R8): `docs/campaigns/phase9/trial-record.md` contains the 13 labelled fields of R8 (date,
  commit, machine and platform, install method (who, by which means, terminal used), install duration, profile,
  trial start, per-screen times (5), first-run time, elapsed minutes, terminal/YAML touched, outcome,
  limits), and states the criterion in Amendment A1's words: from a working install to a first run in
  under fifteen minutes, without a terminal and without YAML. The phase gate refuses an approval whose
  record omits the install method, the install duration or the elapsed minutes from trial start to first
  run (absent, empty or not a value), or whose platform field does not name Linux, macOS or Windows; it is likewise not approved while any other field is empty, while "terminal/YAML touched" is not "no", while the 5 per-screen times and
  the first-run time are not non-decreasing and at or after the trial start, while "elapsed minutes" does
  not equal first-run time minus trial start (to the minute, ±1), or while "elapsed minutes" is 15 or more.

- AC26 (R8): `pyproject.toml`'s runtime dependencies are exactly `aiohttp` and `PyYAML`; its package data
  has a `core.config_ui` line of exactly `strings/*.json` and `profiles/*`. The existing installed-
  distribution node `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`
  (a target, adapted by added assertions only) also asserts that the install target holds
  `core/config_ui/strings/en.json`, `core/config_ui/strings/fr.json` and the 4 profile files under
  `core/config_ui/profiles/`, each byte-identical to the checkout's; its 21-manifest count is unchanged
  (this phase adds no module) and it stays the same single A2 node (`conftest.A2_REAL_NODES` unchanged).
  The full suite passes under the existing AC33 socket guard with 0 new exemptions and 0 new
  process-spawning nodes.

- AC27 (R1): `main(["--install-launcher"], …)` with the platform's launcher directory redirected to a
  temporary directory writes exactly 1 launcher file there (Linux: a `.desktop` file; Windows: a `.cmd`
  file; macOS: a `.command` file, each case exercised by forcing the platform value), whose command
  names `sys.executable` and `-m core.config_ui`, contains 0 occurrences of the access token and of any
  stored credential value, and is not group- or world-writable on POSIX; `serve` is called 0 times and
  the exit status is 0. Running it again replaces the file (1 file afterwards); a pre-existing file
  without the marker line at that path is left byte-identical and the exit status is non-zero.

- AC28 (R5): In one UI process at first run, after screen 4 has written the profile and credential file,
  changing the companion name on screen 1 invalidates screens 1–5 (a request for screen 5 is refused);
  re-passing screens 1–3 and then screen 4 replaces the profile so that it contains the new companion
  name, with exactly 1 profile file in the directory and 1 credential file in the per-user credential
  directory. If instead the profile is
  modified on disk by another writer after screen 4 (1 byte appended), re-passing screen 4 refuses, the
  modified file is byte-identical afterwards, and the cause names the editor as the next action. Once screen 5
  has completed, a new `ConfigUI` instance over the same directory lands the access URL on the console,
  not the wizard (a restart before that is AC30).


- AC29 (R5): For each of the 4 profiles, the wizard's dry run (fake model answering `"bonjour"`) returns a
  run record with a non-empty run id, an outcome and origin `dry_run` naming the fake event it drove; for
  chat Q&A, channel points and spoken keyword the run id equals the `run_id` of the admission scheduler's
  record for that event and of the feed's run started/completed entries, and the outcome is that run's
  completed status; for silent the outcome is "not triggered" with the rejection's reason token. Screen 5
  is not completed, the state file is not marked `completed`, a cause and next action are shown and the
  screen accepts another attempt, when the fake dry-run entry: publishes a run started and a run completed
  event but returns no record; returns a record without a run id, without an outcome, or with an origin
  other than `dry_run`; returns a record whose outcome is not the profile's expected one; or exceeds 60 s
  (fake clock).

- AC30 (R1, R5): Re-entry, each case in a new `ConfigUI` instance over the same directory with fake
  Twitch and model endpoints: (a) after screen 4 wrote the files and before screen 5, the access URL lands
  on the wizard (not the console), screen 1 shows its recorded inputs as passed, the credential renders
  empty and "kept", a request for screen 3, 4 or 5 is refused until screen 2 is passed again, passing
  screens 2–4 makes exactly 1 Twitch validation and 1 model request in the new instance, and screen 5 runs
  its dry run (1 dry-run call); (b) with the fake Twitch endpoint now answering 401 for the stored token,
  screen 2 shows the 401 cause and keeps *next* closed, and a new token submitted on the same screen passes
  it; (c) after a failed screen 5, the wizard lands on the wizard and screen 5 can be retried to success;
  (d) after screen 5 completed, the access URL lands on the console and every wizard route answers without
  a wizard screen (a redirect to the console); (e) with the profile changed by another writer after
  screen 4, the access URL lands on the wizard, screen 4 refuses and leaves the file byte-identical, and
  the cause names the editor. The state file never contains a stored or submitted credential value.

- AC31 (R1): With a fake opener, serve, liveness test, instance probe and clock, on POSIX: a second
  `main([])` while a first instance's record names a live pid and the probe answers the recorded marker
  calls `serve` 0 times, calls the opener exactly 1 time with the recorded access URL, prints the
  "already running" line exactly 1 time and exits 0; the same holds when the first probe attempt exceeds
  2 s and a later attempt within 10 s answers the marker; with the record naming port 8766 and the second
  launch asking for `--port 8765`, the opener receives the 8766 URL and `serve` is called 0 times; with
  an opener that raises, `serve` is still called 0 times and the line with the URL is printed. With a live
  pid whose probe attempts are all refused, exceed 2 s, or answer the wrong-token status until 10 s have
  elapsed (fake clock): more than 1 attempt is made, each bounded to 2 s and none started after 10 s;
  `serve` and the opener are called 0 times; the record is byte-identical afterwards; the printed report
  names the record's path and the observed port fact (answering but not authenticated, or not
  answering) with a cause and a next action and 0 URLs; and the exit status is non-zero. Each of these
  records is stale and yields the
  "did not stop cleanly" report, the record's removal, a fresh complete record carrying this launch's
  marker, 0 opener calls with the old URL, `serve` called exactly 1 time and no exception: (a) a pid that
  is not live; (b) a live pid whose probe answers another marker (a reused pid); (c) a record truncated
  mid-content; (d) a record missing one of its fields. With the platform forced to a non-POSIX value: the
  published record holds pid, port and marker and 0 occurrences of the access token, and no file the
  launch writes holds it; a second launch while that record names a live pid makes 0 probe attempts,
  calls `serve` and the opener 0 times, leaves the record byte-identical, prints the "running but not
  answering" report exactly 1 time naming the record's path and the recorded port with 0 occurrences of
  the token, and exits non-zero; with a pid not live the record is stale as in (a). On POSIX, with the
  probe answering the recorded marker and a product version other than this launch's: `serve` and the
  opener are called 0 times, the record is byte-identical, the report names both versions and the next
  action, and the exit status is non-zero. A fake failure between the temporary write and the publication leaves no file
  under the record's name. Two launches racing on the no-replace publication (fake) yield exactly 1
  `serve` call (the winner's) and, from the loser, 1 opener call with the winner's URL. The record's file
  mode is `0o600` on POSIX; across these cases the access token occurs 0 times in log records,
  diagnostics, response bodies (the probe's included), the supervised child's environment and the status
  record; a clean exit removes the record.

- AC32 (R2, R4, R7): For a child that exits 1 during startup with a stderr tail containing a stored
  credential value and the line `Traceback (most recent call last):`, the Start outcome renders (a) the
  cause: a string-table sentence and next action in the chosen language (French when `fr` is chosen),
  outside any disclosure element, containing 0 characters of the tail; and (b) the raw detail: the tail
  only inside one disclosure element closed by default (no `open` attribute), with the credential value
  replaced by the redaction marker and 0 occurrences of it or its encoded forms anywhere in the response.
  A start failure with an empty stderr tail still renders the cause. The AC5 key check covers the cause
  and the disclosure label; it does not require a key for the tail. No wizard response renders a raw
  detail disclosure (AC17 stands).

## Caller Enumeration

API changes: `UISettings.from_argv` (`--config` becomes optional), `core.config_ui.main` (injectable
opener and serve), `ConfigUI.routes` (new entries), `Supervisor` (environment carries stored credentials
and the feed path). Search method: `grep -rn "UISettings\.from_argv\|UISettings(\|Supervisor(\|config_ui.main\|\.routes\b"`
over `core/`, `modules/`, `demo/`, `tests/`; dynamic or reflective callers are not detected.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/config_ui/__init__.py | `main()` | Accept no `--config`; call the opener once after startup checks; a record whose pid is live and whose `/instance` probe authenticates and confirms its marker makes the launch open it and exit 0 without serving when the versions match, and report both versions, open nothing and exit non-zero when they differ; where no token is persisted a live pid is reported as running but not answering, kept, and nothing is served; an incomplete record, a pid not live or a probe answering another marker is removed as stale before serving; a live pid the probe does not confirm within 10 s is reported, kept, and nothing is served; `--install-launcher` writes the launcher and exits; unchanged when `--config` given |
| core/config_ui/__init__.py | `ConfigUI.__init__` (`Supervisor(default_launch_argv(...), ...)`) | Pass the merged environment (stored credentials + feed path) |
| core/config_ui/__main__.py | `main()` entry | No change (delegates to `main`) |
| pyproject.toml | console script `twitch-ia-compagnon-config-ui` | No change; now valid with no argument |
| tests/test_config_ui.py | `UISettings.from_argv`, `Supervisor(...)` (10 sites), `ui.routes` | Existing calls keep passing `--config`/explicit environ; the route-set assertion is allowlisted below |
| tests/test_input_ownership.py, tests/test_main.py, tests/test_status_record.py | `UISettings(...)` / `from_argv` | Keep passing `config=`; no change expected |

`core.main`'s own command line is unchanged (`--config` stays required there:
`tests/test_main.py` keeps asserting it).

## Test failure allowlist

### Amendment A2 (2026-10-25) — one named exemption for POSIX-only subjects

The closing gate found 18 uses of a single `posix_only` marker plus one `pytest.skip`, and this phase's
invariant forbids new skips. The invariant as written is too absolute: a test whose **subject** is a POSIX
facility — file modes, owners, per-user directory checks — has no equivalent on a platform that does not
have it, and forcing the platform *value* does not create the facility. A silent skip would hide that; an
explicit failure would make the suite red on a platform the project does not support.

A2 therefore permits **exactly one exemption class**: a single named marker for tests whose subject is a
POSIX-only facility, carrying a reason, used only on such tests. It is enumerated in one place, its use
count and node list are asserted by an invariant test, and adding a node to it is a visible diff. Every
other skip, xfail or importorskip stays forbidden, and tests of *platform behaviour* — the Windows
directory and ACL branches, the launcher's per-platform contents — must run on any platform by forcing the
platform value. A test that needs a native facility it cannot have is exempted, never failed.

Each entry fails only because R1 makes `--config` optional or R1/R7 add routes; the adaptation keeps
the assertion's strength.

- `tests/test_config_ui.py::test_a_missing_config_or_bad_port_exits_non_zero` — asserts `main([]) != 0`;
  R1 makes a no-argument start valid (A2 default location). The adapted test keeps both bad-port
  assertions and asserts a no-argument start with an invalid default (e.g. a collision) still exits
  non-zero, with `serve` replaced as today.
- `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record` — asserts
  the route set is exactly the 7 existing routes; R1/R7 add the wizard, console, feed, start, stop,
  revoke, language and the token-authenticated `/instance` routes. The adapted set lists them, so the test's secret sweep covers every new
  route.
- `tests/test_docs_bilingual.py::test_the_discovered_pairs_are_exactly_the_declared_guides` — the
  discovered pairs gain `first-run` (R8); `GUIDES` gains `first-run`.

Not an allowlist entry (it does not fail; AC26 strengthens it):
`tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`
gains assertions that the 2 string files and the 4 profiles are installed byte-identical; nothing in it
is removed or loosened, its 21-manifest count stays, and its companion
`test_install_test_skips_only_when_pip_is_unavailable` still holds over the adapted source.
