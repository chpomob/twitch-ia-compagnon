---
name: "phase4-local-config-ui"
version: "1.0"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec, phase4, config-ui]
targets:
  - file: core/config_ui/__init__.py
    description: "New package: the separate configuration-UI process — bind policy, access token and session, CSRF guard, base page, generated module pages, Check, overlay write/remove, supervised restart and drift display; names no module."
  - file: core/config_ui/__main__.py
    description: "New: `python -m core.config_ui` entry point — parses --config, --overlay, --host, --port, --allow-non-loopback, the repeatable --allowed-host, --status-file and an optional launch argv after `--`, then serves the UI."
  - file: core/main.py
    description: "Merge the overlay file over the base configuration (implicit sibling path or --overlay) in load_config/run/check_config and --check-config; write a secret-free status record when the status-file environment variable is set."
  - file: core/contracts.py
    description: "Accept `default` as a non-constraining schema annotation (beside `title`/`description`) and refuse a `default` that does not satisfy its own schema node."
  - file: modules/agent_link/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/audio_input/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/audio_output/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/audit/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/brain/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/capture/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/chat_context/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/clips/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/kick/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/moderation/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/proxy/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/stream_control/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/twitch/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/users/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/viewer_memory/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/watch/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: modules/youtube/module.yaml
    description: "Add `title` to every settings_schema node and `default` wherever the prose documents one."
  - file: pyproject.toml
    description: "Add a second [project.scripts] console entry for the configuration UI; runtime dependencies stay exactly aiohttp and PyYAML."
  - file: .gitignore
    description: "Ignore operator overlay files (`*.local.yaml`) and status records (`*.status.json`)."
  - file: tests/test_config_ui.py
    description: "New: socket-free tests of bind policy, token/CSRF/Host guards, generated pages for all 17 modules, Check, overlay write/remove, apply/drift, and the secret-canary sweep."
  - file: tests/test_overlay.py
    description: "New: merge precedence, overlay path derivation, removal of an override, and runtime/--check-config parity with the overlay."
  - file: tests/test_manifest_presentation.py
    description: "New: every settings_schema node of the 17 manifests has a title; documented defaults are machine-readable and valid; `default` annotation contract in core/contracts.py."
  - file: tests/test_users.py
    description: "Adapt the exact-dict assertion on `max_channels` so it tolerates the new `title`/`default` annotations (allowlisted below)."
  - file: docs/config-ui.md
    description: "New operator doc: launching the UI, local-only statement, token, overlay path and merge precedence, secrets policy, restart/supervision and drift semantics."
---

# Phase 4 — local web configuration UI, one page per module

## Problem

Configuring the companion today means hand-writing a ~500-line YAML file (the shipped
`presence.yaml.example` is 583 lines) plus up to 25 environment variables, then discovering
errors one at a time through `--check-config`, which prints nothing on success. The
operator-facing knowledge (labels, defaults, meaning of each setting) lives only in prose
comments and descriptions: the 17 shipped manifests carry 0 `title` keys and no machine-readable
default. Nothing shows what is enabled, what is ready, which values come from where, or whether
the running process uses what is on disk.

The operator has decided (binding decisions 1b/2a/3a/4a/5a/6b of the brief): a **separate local
process** (`python -m core.config_ui`) serves a small web UI; it writes only to an **overlay file**
merged over the untouched base file; it **never edits or reveals secret values**; applying a
change is a **supervised restart**; every module page is **generated from the module's manifest
schema** (modules ship no HTML); v1 covers read, validate, base page, one page per module, write
(except secrets) and restart.

### Assumptions (documented, made where the brief is silent)

- A1. Overlay path: the base file name with a trailing `.example` removed and its final
  `.yaml`/`.yml` suffix replaced by `.local.yaml`, in the base file's directory
  (`config.yaml` → `config.local.yaml`, `presence.yaml.example` → `presence.local.yaml`). An
  explicit `--overlay PATH` overrides this on both the runtime and the UI. A base name ending in
  neither `.yaml` nor `.yml` (after removing `.example`) has no implicit overlay.
- A2. The UI edits only `enabled_modules` membership and `modules.<name>` settings. The
  top-level `secrets`, `triggers`, `actions`, `limits` and `modules_directory` blocks are shown
  read-only in v1.
- A3. Environment variables are never edited. "Set/unset" is evaluated against the UI process's
  own environment, which is the environment a UI-launched main process inherits.
- A4. The UI supervises (stops/restarts) only a main process it launched itself. A main process
  started elsewhere is reported but never signalled.
- A5. The main process communicates its state to the UI through a status record file whose path
  it receives in an environment variable (documented in `docs/config-ui.md`); without that
  variable the runtime writes nothing and behaves exactly as before. The UI watches exactly one
  status path: `--status-file PATH` when given, otherwise the base file's full name with
  `.status.json` appended, in the base file's directory (`config.yaml` →
  `config.yaml.status.json`). The UI sets the variable to that path for every process it
  launches. A main process started elsewhere publishes a record the UI can see only if the
  operator sets the same variable to that same path when starting it; otherwise the UI knows of
  no running process (drift "unknown"). Supervision is decided by pid: a record whose pid is not
  the pid of the child this UI launched is "not supervised".
- A8. Accepted client authorities (for `Host` and `Origin`): the bound host with the bound port;
  when the bound host is a loopback address or `localhost`, additionally `localhost`,
  `127.0.0.1` and `[::1]` with the bound port; when the bound host is a wildcard (`0.0.0.0` or
  `::`), the loopback authorities above plus each `--allowed-host NAME` (repeatable) with the
  bound port. The printed access URL uses the bound host, or `127.0.0.1` for a wildcard bind.
  Host names compare case-insensitively; IPv6 literals appear in brackets.
- A6. Machine-readable presentation metadata lives inside `settings_schema` as the `title`
  annotation (already accepted) and a new `default` annotation; no new top-level manifest key is
  introduced, so the loader's manifest allowlists and `DECLARATION_KEYS` in
  `tests/test_examples.py` do not change.
- A7. The per-action authorization-rule view mentioned in the brief's Problem is out of scope for
  v1 (no requirement in the brief's R-UI list asks for it).

Requirement classification: R1–R8 are non-trivial (8, at the cap); R9 and R10 are trivial
static constraints.

## Requirements

- R1: `python -m core.config_ui --config PATH` (and an equivalent console script) starts a
  separate process serving the UI. It binds a loopback address by default and refuses to start —
  with a diagnostic and a non-zero exit status, before binding anything — when asked to bind a
  non-loopback host unless `--allow-non-loopback` is also given. At startup it generates a fresh
  random access token (at least 128 bits of entropy) and prints the access URL once on its
  standard output. Every request without a valid session (token or session cookie derived from
  it) is refused with 401/403 and no page content. Every state-changing request (write, remove,
  check-with-draft, apply) must be a POST carrying a valid per-session CSRF token. Every request
  must carry a `Host` header whose authority is one of the accepted client authorities (A8), or
  it is refused with 403. A state-changing request carrying an `Origin` header is refused with
  403 unless that header parses as exactly `http://<authority>` (no path; an explicit port equal
  to the default 80 is equivalent to omitting it) with an accepted authority; `Origin: null` and
  any other scheme are refused; an absent `Origin` is allowed. GET never changes state. All
  request-handling behaviour and the bind policy are testable without opening any socket.
- R2: The runtime (`core.main.run`, `load_config`, `check_config` and the `--check-config` CLI)
  reads the overlay (path per A1, or `--overlay PATH`) and deep-merges it over the base before
  `${NAME}` resolution and validation, with this precedence: a mapping in the overlay merges key
  by key into the base mapping, recursively; any other overlay value (scalar, list, null)
  replaces the base value wholesale; keys only in the base are kept. An absent overlay file
  leaves behaviour byte-for-byte identical to today; an empty overlay file means no override; an
  overlay that is unreadable, not valid YAML or not a mapping is a configuration error naming the
  overlay file and never quoting its content. Relative paths in the merged configuration resolve
  from the base file's directory. The base file is never written by any component.
- R3: Every node of every shipped manifest's `settings_schema` that describes a setting (the root
  and each entry under `properties`, recursively, including `items` sub-schemas that have
  `properties`) carries a non-empty `title`; every setting whose description documents a default
  ("Default X") carries a machine-readable `default` equal to X. The schema validator accepts
  `default` as an annotation with no constraint effect on validated values, and refuses — naming
  the schema path — a `default` that does not itself satisfy the node's schema. Descriptions
  remain as help text. A property literally named `default` inside `properties` stays a property,
  not an annotation.
- R4: The UI serves (a) a base page listing every discovered module (disabled ones included) with
  its enabled state, its readiness, and a link to its own page, plus the configuration's origin:
  base file path, overlay path and whether it exists, and each referenced environment variable's
  name with set/unset state; and (b) one page per discovered module, generated solely from its
  manifest (`name`, `settings_schema` with `title`/`description`/`default`, declared
  `credentials`) and the merged configuration, with no UI code naming any module. Pages render
  the schema subset `type` (object, array, string, integer, number, boolean), `properties`,
  `required`, `additionalProperties` (false → no extra keys; a schema → named entries editor),
  `enum`, `items`, `minimum`, `maximum`. Each field shows its label, help text, documented
  default, current value, and origin (base / overlay / environment reference / default-not-set).
  Any node the page cannot render as a control (object without `properties` or schema
  `additionalProperties`, array without `items`, type `null`, a manifest without
  `settings_schema`) is shown with an explicit "not editable here" notice naming its setting path
  and, read-only, its unresolved configured text — never silently omitted. Readiness is the
  configuration-level verdict of the last Check plus, when a status record from a running
  process exists (R7), that process's per-module ready/degraded state with the record's time.
- R5: A Check action, per module page and on the base page, validates a draft (the merged
  on-disk configuration plus the page's unsaved edits) without writing the overlay, without
  signalling or contacting the main process, and without opening a socket, through the existing
  check path (`core.main.check_config` with an injected `environ`). It reports every diagnostic
  it can in one run: all unresolved `${NAME}` references of the top-level blocks and of enabled
  modules (each by setting path and variable name), and — when none is unresolved — every
  diagnostic of the module-settings phase; when the settings phase could not be reached, the
  result says so explicitly. A module page shows the diagnostics that name that module; the base
  page shows all.
- R6: Save writes module-settings edits and `enabled_modules` changes to the overlay only; the UI
  refuses to write any path other than the managed overlay path resolved at startup, and refuses
  to start if that path equals the base path. A write replaces the overlay atomically (a reader
  sees the old or the new file, never a partial one), is refused when the overlay changed on
  disk since the page was rendered (stale-write detection), and rejects values that violate the
  field's own schema, returning per-field diagnostics and writing nothing. Remove-override
  deletes a single key path from the overlay (pruning emptied parent mappings), after which the
  effective value is the base value again. Declared credential fields, fields whose configured
  value is a `${NAME}` reference, and the read-only blocks of A2 are not writable. Every accepted
  write or removal emits one log record with time, module, operation and the setting paths
  touched — never values.
- R7: Apply performs a supervised restart using a declared launch argv (given after `--` on the
  UI command line; default: the current interpreter running `core.main --config <base>` and the
  same `--overlay` if given), executed without a shell. Apply is refused unless a Check of the
  on-disk configuration passes. The UI stops the process it launched (terminate, bounded wait,
  then kill), starts the new one with the status-file variable set, and reports within a bounded
  window (documented default 60 s) one of: accepted (the new process's status record reports
  ready with a configuration digest equal to the on-disk digest), refused (the process exited;
  exit status and its value-free diagnostics shown), or unknown (window elapsed). The main process
  writes the status record atomically on becoming ready and on each module ready/degraded
  transition, containing its pid, start time, a digest of the merged unresolved configuration
  document it loaded, and per-module state — never a resolved or credential value. Every page
  shows whether disk and the running process agree: in sync, differs, or unknown (no record, or
  the recorded pid is not alive), and a process not launched by this UI is labelled as not
  supervised and is never signalled. Status records are discovered only at the status path of
  A5.
- R8: No secret value appears in any HTML page, JSON response, UI log record, restart report, or
  diagnostic produced by the UI. Secret values are: the resolved value of every environment
  variable referenced anywhere in the base or overlay, the values of variables listed in the
  `secrets` block, and any literal value at a declared credential path. Such fields are displayed
  only as their reference (`${NAME}`) or as "literal value configured (hidden)", with set/unset
  state. The access token appears only in the single startup line on standard output.
- R9: No runtime dependency is added: `[project].dependencies` stays exactly `aiohttp` and
  `PyYAML`. Every page works offline: no response makes the browser load, execute or submit to
  anything from another host — no external URL in any resource-loading or navigating position
  (`src`, `href`, form `action`, CSS `url(...)` or `@import`, script-initiated requests); all
  assets are served by the UI process itself. Configured values and unresolved configured text
  displayed per R4 are data, not references: they may contain external URLs, but are rendered
  only as HTML-escaped text (or JSON string values), never as a link, resource reference or
  markup.
- R10: `docs/config-ui.md` documents how to launch the UI, that it is local-only and does not
  administer a remote brain over the proxy, the overlay path rule (A1) and the merge precedence
  (R2), the secrets policy (R8), the restart/supervision and drift semantics (R7), and the
  status-file variable name and default status path (A5) with how to make an externally
  started main process visible to the UI; the UI's
  base page states that it is local-only.

## Acceptance criteria

- AC1 (R1): With `--host 0.0.0.0` (or `192.168.1.10`, or `::`) and no `--allow-non-loopback`,
  the UI exits with a non-zero status and a diagnostic, and a test proves no socket was bound;
  with `127.0.0.1`, `::1` or `localhost` the bind policy accepts; with `--allow-non-loopback` a
  non-loopback host is accepted and the token requirement still applies.
- AC2 (R1): Two UI starts produce two different tokens, each ≥ 22 URL-safe characters (≥ 128
  bits); the access URL containing the token is printed exactly once on standard output and
  appears in no log record.
- AC3 (R1): For each of the base page, a module page, and each JSON endpoint, a request with no
  token/session returns 401 or 403 and a body containing none of the page's module names or
  setting paths; the same request with a valid session returns 200.
- AC4 (R1): A POST write with a valid session but a missing or wrong CSRF token, or with a
  `Host` or `Origin` authority outside the accepted set of A8, returns 403 and leaves the overlay
  file's bytes unchanged; a GET to any write/apply endpoint returns 405 and changes nothing.
- AC5 (R1): The whole UI test module runs with socket creation patched to raise, and passes.
- AC6 (R2): Given base `{a: {b: 1, c: [1, 2]}, d: x}` and overlay `{a: {b: 2, c: [3]}, e: y}`,
  the merged document is exactly `{a: {b: 2, c: [3]}, d: x, e: y}`; an overlay `a: null` yields
  `a: null` in the merged document.
- AC7 (R2): Overlay path derivation yields `config.local.yaml` for `config.yaml`,
  `presence.local.yaml` for `presence.yaml.example`, `x.local.yaml` for `x.yml`, none for
  `config.txt`, and the `--overlay PATH` value when given.
- AC8 (R2): For a profile whose overlay changes one enabled module's integer setting to an
  invalid value (e.g. below its `minimum`), both `check_config` and `--check-config` return 2 and
  name that module and field; removing the overlay makes both return 0; `run` with the valid
  overlay hands the module the overlay value.
- AC9 (R2): An overlay whose content is `[1, 2]`, or invalid YAML containing the text
  `CANARY-OVERLAY`, makes `check_config` return 2 with a diagnostic naming the overlay file and
  not containing `CANARY-OVERLAY`; an existing empty overlay file gives the same result as no
  overlay.
- AC10 (R2): With no overlay file present, the existing suite (`.venv/bin/python -m pytest tests/
  -q -p no:cacheprovider`) reports ≥ 2,460 passed and ≤ 18 skipped plus the tests added here,
  with no failure outside the allowlist below.
- AC11 (R3): For all 17 shipped manifests, 100% of setting nodes (root, every `properties`
  entry recursively, and every `items` sub-schema having `properties`) have a non-empty string
  `title`; the test counts the nodes checked per manifest and the total is > 0 for each of the 17.
- AC12 (R3): For every setting whose `description` matches `Default <literal>.`, a `default`
  annotation is present and equal to that literal parsed as YAML (e.g. audio_output
  `max_text_chars` → `400`, `synthesis.timeout_seconds` → `10`, `synthesis.probe` → `true`); every
  declared `default` validates against its own node.
- AC13 (R3): `validate_schema` accepts `{type: integer, minimum: 1, default: 5}`, refuses
  `{type: integer, minimum: 1, default: 0}` and `{type: string, default: 3}` with a diagnostic
  naming `<label>.default`; validating the value `7` against a node with `default: 5` gives the
  same result as without the annotation; the audio_output `voices` schema still has exactly the
  two properties `allowed` and `default`.
- AC14 (R4): The base page lists exactly the 17 discovered modules for each of the four shipped
  example profiles, each with its enabled state matching `enabled_modules`, a working link to its
  page (GET returns 200), the base path, overlay path with exists/absent, and each referenced
  variable name with set/unset.
- AC15 (R4): Each of the 17 module pages renders a control for every renderable setting with its
  `title` as label, its description as help text, and its documented default; a test asserts, per
  module, that the set of setting paths shown (controls plus "not editable here" notices) equals
  the set of setting paths in the schema — no path silently dropped.
- AC16 (R4): A fixture module added only as a new `module.yaml` (not among the 17) gets a
  working page with its titled fields and no UI code change; a static test asserts that no UI
  source file contains any of the 17 module names as a string literal.
- AC17 (R4): A fixture schema containing an object without `properties`, an array without
  `items` and a `null`-typed field renders each with a "not editable here" notice naming its
  setting path; an `enum` field offers exactly its members; an integer field with `minimum: 1,
  maximum: 10` exposes both bounds; a `required` field is marked required.
- AC18 (R4): With a status record reporting module M `degraded`, M's readiness on the base page
  shows degraded with the record time; with no record, readiness shows only the last Check
  verdict and states that no running process is known.
- AC19 (R5): A draft with 3 unresolved `${NAME}` references across two enabled modules reports
  3 diagnostics (each with setting path and variable name) in one Check and states that the
  settings phase was not reached; a draft with 0 unresolved references and 2 invalid settings in
  two enabled modules reports both diagnostics in one Check.
- AC20 (R5): During Check the overlay file's bytes and mtime are unchanged, no process is
  signalled or started, and no socket is created (socket creation patched to raise).
- AC21 (R5): A module page's Check lists only the diagnostics naming that module; the base
  page's Check lists all of them.
- AC22 (R6): Saving setting S of module M writes an overlay containing exactly the override at
  `modules.M.S` (plus previously existing overrides), the base file's bytes are unchanged, and a
  subsequent `load_config` yields the new value; a log record lists `modules.M.S` and contains no
  value.
- AC23 (R6): Remove-override on `modules.M.S` makes the effective value equal the base value
  again and removes `modules.M` from the overlay when it becomes empty.
- AC24 (R6): A save carrying an out-of-range integer or a wrong-typed value returns a per-field
  diagnostic and leaves the overlay bytes unchanged; a save after the overlay was modified on
  disk since the page render is refused as stale and writes nothing.
- AC25 (R6): Attempts to write a declared credential field, a field whose configured value is a
  `${NAME}` reference, the `secrets` block, or any path outside `modules.<name>` /
  `enabled_modules` are refused and change no file; starting the UI with `--overlay` equal to
  the base path exits non-zero.
- AC26 (R6): A write interrupted after the new content is prepared but before it replaces the
  overlay (simulated failure) leaves the previous overlay content intact and parseable.
- AC27 (R7): With a failing on-disk Check, Apply is refused and no process is stopped or started.
- AC28 (R7): With a fake launch argv whose child writes a status record reporting ready and the
  current on-disk digest, Apply reports accepted; with a child exiting 2 after printing a
  value-free diagnostic, Apply reports refused with exit status 2 and that diagnostic; with a
  child writing nothing within a window shortened to 1 s, Apply reports unknown. The launch argv
  is executed without a shell (an argv element `;echo x` is passed literally).
- AC29 (R7): Stopping the supervised child sends terminate, then kill after the bounded wait
  when the child ignores terminate; a record written at the UI's status path (A5) by a live
  process whose pid differs from any child this UI launched makes the pages show that process's
  per-module state labelled "not supervised", and Apply never signals that pid. The default
  status path for base `config.yaml` is `config.yaml.status.json` in the same directory, and
  `--status-file PATH` overrides it; a record written at any other path is not reported.
- AC30 (R7): After an overlay save not yet applied, every page shows "differs"; after a
  successful Apply it shows "in sync"; with a record whose pid is not alive it shows "unknown".
- AC31 (R7): `run` with the status-file variable set writes a record containing pid, start
  time, digest and per-module state, and whose serialized text contains none of the canary values
  of AC32; `run` without the variable writes no file.
- AC32 (R8): With environment canaries (e.g. `CANARY-ENV-7f3a`) for every referenced variable
  including those in `secrets`, and a literal canary at a declared credential path, a test fetches
  the base page, all 17 module pages, every JSON endpoint, performs a Check, a save, a removal and
  an Apply (accepted and refused), collects all response bodies, headers, log records and restart
  reports, and asserts that none contains any canary; the credential fields display `${NAME}` or
  "literal value configured (hidden)".
- AC33 (R9): `[project].dependencies` lists exactly `aiohttp` and `PyYAML`; `[project.scripts]`
  contains the existing `twitch-ia-compagnon` entry plus one entry targeting the UI's main. For
  every UI response, no `src`, `href` or form `action` attribute value, CSS `url(...)` or
  `@import` target, and no URL in served script code points to a host other than an accepted
  authority (A8). With a non-secret string setting configured as
  `https://cdn.example.invalid/x.js"><script>` the module page shows that text HTML-escaped
  (the literal `&lt;script&gt;` appears, no new element is created) and the text appears in no
  `src`/`href`/`action` attribute.
- AC34 (R10): `docs/config-ui.md` exists and contains the launch command `python -m
  core.config_ui`, the word "local-only" (or "local only"), the overlay path rule with the two
  examples of AC7, the merge precedence of AC6, the secrets policy and the three drift states
  (in sync, differs, unknown), and the status-file variable name with the default status path
  rule of A5; the base page contains a local-only statement.
- AC35 (R4): Given a fixture module with settings `a`, `b`, `c`, `d` (each with a documented
  `default: 5`), base `modules.M: {a: 1, c: "${VAR_C}", d: 5}` and overlay `modules.M: {a: 2}`,
  the module page shows, per field: `a` current value `2` with origin overlay (not base); `c`
  origin environment reference showing `${VAR_C}` and its set/unset state, never the resolved
  value; `d` current value `5` with origin base (a configured value equal to the default is not
  reported as default); `b` origin default-not-set with its documented default `5` shown as the
  default and no current configured value. Removing the overlay override on `a` changes its
  origin to base with value `1`.
- AC36 (R1): With bind `127.0.0.1:8765`, requests whose `Host` is `127.0.0.1:8765`,
  `localhost:8765`, `LOCALHOST:8765` or `[::1]:8765` are accepted, and `evil.example:8765`,
  `127.0.0.1:9999` or a missing `Host` get 403 (GET included); a POST with
  `Origin: http://localhost:8765` is accepted, and with `Origin: null`,
  `Origin: https://127.0.0.1:8765`, `Origin: http://evil.example:8765` or
  `Origin: http://127.0.0.1:8765/x` gets 403. With bind `0.0.0.0:8765`,
  `--allow-non-loopback` and `--allowed-host box.lan`, `Host: box.lan:8765` is accepted,
  `Host: other.lan:8765` gets 403, and the printed URL uses `127.0.0.1:8765`.

## Test failure allowlist

- `tests/test_users.py::test_manifest_is_v2_and_declares_users_read_without_granting_it` — it
  asserts `schema["properties"]["max_channels"]` equals exactly `{type, minimum, description}`;
  R3 requires a `title` on that node, so the exact-dict assertion contradicts R3. The adaptation
  keeps the `type: integer` and `minimum: 1` checks.

## Caller Enumeration

API changes: `core.main.load_config`, `core.main.check_config` and `core.main.run` gain overlay
support (an optional overlay-path keyword; existing positional/keyword usage keeps working with
the implicit A1 path), `core.main.main` gains `--overlay`, and `core.contracts.validate_schema`
accepts the `default` annotation. Search method: `grep -rn "load_config(\|check_config(\|await
run(\|validate_schema(" --include=*.py core modules tests`; blind spot: calls through aliases or
`getattr`.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/main.py | `run`, `check_config`, `main` → `load_config` | Pass the overlay path through; no change for callers that omit it |
| core/config_ui/ (new) | Check path → `check_config` | Calls with the draft and injected `environ` |
| tests/test_main.py (29 call sites) | `run`, `check_config`, `load_config` | None; tmp_path profiles have no overlay file |
| tests/test_profiles.py (7) | `check_config`, `load_config` | None |
| tests/test_examples.py (5) | `load_config`, `check_config` | None; repository has no `*.local.yaml` (gitignored) |
| tests/test_integration.py (2) | `run` | None |
| tests/test_shutdown.py (2) | `run` | None |
| core/contracts.py (6 internal) | `validate_schema` recursion | Carry the `default` check through nested nodes |
| core/loader.py (1) | `validate_schema` on `settings_schema` | None; accepts the new annotation |
