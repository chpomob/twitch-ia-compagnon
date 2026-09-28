---
name: "phase4-local-config-ui"
version: "1.0"
author: "adversarial-spec"
status: "draft"
tags: [adversarial, spec, phase4, config-ui]
targets:
  - file: core/config_ui/__init__.py
    description: "New package: the separate configuration-UI process — bind policy, access token and session, Host/Origin/CSRF guards, base page, core-settings page, one generated page per discovered module (settings and trigger policies), read-only secrets/actions views, Check, overlay write/remove within the 6c scope, supervised restart, status-record reading and drift display; names no module."
  - file: core/config_ui/__main__.py
    description: "New: `python -m core.config_ui` entry point — parses --config, --overlay, --host, --port, --allow-non-loopback, the repeatable --allowed-host, --status-file and an optional launch argv after `--`, then serves the UI."
  - file: core/overlay.py
    description: "New: the one overlay implementation shared by the runtime and the UI — overlay path derivation (A1), reading/refusing an overlay, and the deep merge with the R2 precedence."
  - file: core/main.py
    description: "Merge the overlay over the base in load_config/run/check_config and add `--overlay` to the CLI; publish the secret-free status record (A5) when the status-file variable is set, on becoming ready and on every module ready/degraded transition; expose the limit-group table as a public read-only declaration the UI renders from."
  - file: core/contracts.py
    description: "Accept `default` as a non-constraining schema annotation (beside `title`/`description`) and refuse a `default` that does not satisfy its own schema node."
  - file: modules/agent_link/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/audio_input/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/audio_output/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/audit/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/brain/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/capture/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/chat_context/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/clips/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/kick/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/moderation/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/proxy/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/stream_control/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/twitch/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/users/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/viewer_memory/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/watch/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: modules/youtube/module.yaml
    description: "Add `title` to every settings_schema setting node and a machine-readable `default` wherever the description documents one."
  - file: pyproject.toml
    description: "Add a second [project.scripts] console entry targeting the UI's main; runtime dependencies stay exactly aiohttp and PyYAML."
  - file: .gitignore
    description: "Ignore operator overlay files (`*.local.yaml`) and status records (`*.status.json`)."
  - file: tests/test_config_ui.py
    description: "New: socket-free tests of bind policy, token/CSRF/Host/Origin guards, base/core/module pages for all 17 modules, read-only secrets/actions views, Check, overlay write/remove within the 6c scope, apply/drift, and the secret-canary sweep."
  - file: tests/test_overlay.py
    description: "New: overlay path derivation, merge precedence, removal of an override, refused overlays, and runtime/--check-config parity with the overlay."
  - file: tests/test_status_record.py
    description: "New: status-record publication on becoming ready and on every module ready/degraded transition, atomic replacement, sequence ordering, opt-out without the variable, and absence of secret values."
  - file: tests/test_manifest_presentation.py
    description: "New: every settings_schema setting node of the 17 manifests has a title; documented defaults are machine-readable and valid; the `default` annotation contract of core/contracts.py."
  - file: tests/test_users.py
    description: "Adapt the exact-dict assertion on `max_channels` so it tolerates the new `title`/`default` annotations (allowlisted below)."
  - file: tests/test_main.py
    description: "Adapt the exact `[project.scripts]` assertion to the two console entries (allowlisted below); the dependency assertion is unchanged."
  - file: docs/config-ui.md
    description: "New operator doc: launching the UI, local-only statement, token, overlay path and merge precedence, what the UI writes and what stays read-only (6c), secrets policy, restart/supervision, status record and drift semantics."
---

# Phase 4 — local web configuration UI, one page per module

## Problem

Configuring the companion today means hand-writing a ~500-line YAML file (the shipped
`presence.yaml.example` is 583 lines) plus up to 25 environment variables, then discovering
errors one at a time through `--check-config`, which prints nothing on success. The
operator-facing knowledge (labels, defaults, meaning of each setting) lives only in prose
comments and descriptions: the 17 shipped manifests carry 0 `title` keys and no usable
machine-readable default. Nothing shows what is enabled, what is ready and why not, which value
comes from where, which declared actions have no authorization rule, or whether the running
process uses what is on disk.

The operator's binding decisions: a **separate local process** (`python -m core.config_ui`,
1b) serves a small web UI; it writes only to an **overlay file** merged over the untouched base
file (2a); it **never edits or reveals secret values** (3a); applying a change is a **supervised
restart** (4a); every module page is **generated from the module's manifest** — modules ship no
HTML (5a); v1 covers read, validate, base page, one page per module, write and restart (6b); and
the UI **writes** exactly `enabled_modules`, `modules.<name>` settings, `triggers`, `limits` and
`modules_directory`, while `secrets` and `actions` are **displayed read-only** so a mis-click
can never widen the authorized actions (6c).

### Assumptions (documented, made where the brief is silent)

- A1. Overlay path: the base file name with a trailing `.example` removed and its final
  `.yaml`/`.yml` suffix replaced by `.local.yaml`, in the base file's directory
  (`config.yaml` → `config.local.yaml`, `presence.yaml.example` → `presence.local.yaml`). An
  explicit `--overlay PATH` overrides this on both the runtime and the UI. A base name ending in
  neither `.yaml` nor `.yml` (after removing `.example`) has no implicit overlay: the runtime then
  reads the base alone, and the UI refuses to start unless `--overlay PATH` is given (R6).
- A2. Pages. The base page lists modules and the configuration's origin (R4). A **core-settings
  page**, linked from the base page, holds the top-level blocks: `modules_directory` and
  `limits` (editable), `secrets` and `actions` (read-only). `enabled_modules` is edited from the
  base page (one enabled toggle per module). The trigger policies of `triggers.<input>` are
  edited on the page of the module named `<input>`, generated from that module's manifest
  `triggers` declaration (its trigger types' `parameter_schema` and its supported combination
  operators). No page is written by hand for any module.
- A3. Environment variables are never edited. "Set/unset" is evaluated against the UI process's
  own environment, which is the environment a UI-launched main process inherits.
- A4. The UI supervises (stops/restarts) only a main process it launched itself. A main process
  started elsewhere is reported but never signalled.
- A5. The main process publishes its state through a status record file whose path it receives
  in an environment variable (name documented in `docs/config-ui.md`); without that variable the
  runtime writes nothing and behaves exactly as before. The UI watches exactly one status path:
  `--status-file PATH` when given, otherwise the base file's full name with `.status.json`
  appended, in the base file's directory (`config.yaml` → `config.yaml.status.json`). The UI
  sets the variable to that path for every process it launches. A main process started elsewhere
  is visible only if the operator sets the same variable to that same path. Supervision is
  decided by pid: a record whose pid is not the pid of the child this UI launched is "not
  supervised".
- A6. Machine-readable presentation metadata lives inside `settings_schema` as the `title`
  annotation (already accepted by the validator) and a new `default` annotation; no new
  top-level manifest key is introduced, so the loader's manifest allowlists and the
  `DECLARATION_KEYS` pinned by `tests/test_examples.py` do not change.
- A7. Deep merge cannot delete a key the base file defines (R2). An entry defined in the base
  (for example a trigger channel policy) can be overridden through the overlay but not deleted;
  the page states this for such entries instead of offering a delete that would silently not
  happen.
- A8. Accepted client authorities (for `Host` and `Origin`): the bound host with the bound port;
  when the bound host is a loopback address or `localhost`, additionally `localhost`,
  `127.0.0.1` and `[::1]` with the bound port; when the bound host is a wildcard (`0.0.0.0` or
  `::`), the loopback authorities above plus each `--allowed-host NAME` (repeatable) with the
  bound port. The printed access URL uses the bound host, or `127.0.0.1` for a wildcard bind.
  Host names compare case-insensitively; IPv6 literals appear in brackets.
- A9. If the operator hand-writes `secrets` or `actions` into the overlay, the runtime merges
  them like any other key (the overlay is the operator's file); the UI shows them read-only with
  origin "overlay", never changes them, and preserves them unchanged across every UI write.

Requirement classification: R1–R8 are non-trivial (8, at the cap); R9 and R10 are trivial
static constraints.

## Requirements

- R1: `python -m core.config_ui --config PATH` (and an equivalent console script) starts a
  separate process serving the UI. It binds a loopback address by default and refuses to start —
  with a diagnostic and a non-zero exit status, before binding anything — when asked to bind a
  non-loopback host unless `--allow-non-loopback` is also given. At startup it generates a fresh
  random access token (at least 128 bits of entropy) and prints the access URL once on its
  standard output. Every request without a valid session (token, or a session cookie derived
  from it) is refused with 401/403 and no page content. Every state-changing request (save,
  remove-override, check-with-draft, apply) must be a POST carrying a valid per-session CSRF
  token. Every request must carry a `Host` header whose authority is one of the accepted client
  authorities (A8), or it is refused with 403. A state-changing request carrying an `Origin`
  header is refused with 403 unless it parses as exactly `http://<authority>` (no path; an
  explicit port 80 is equivalent to omitting it) with an accepted authority; `Origin: null` and
  any other scheme are refused; an absent `Origin` is allowed. GET never changes state. All
  request handling and the bind policy are testable without opening any socket.
- R2: The runtime (`core.main.run`, `load_config`, `check_config` and the `--check-config` CLI)
  reads the overlay (path per A1, or `--overlay PATH`) and deep-merges it over the base before
  `${NAME}` resolution and validation, with this precedence: a mapping in the overlay merges key
  by key into the base mapping, recursively; any other overlay value (scalar, list, null)
  replaces the base value wholesale; keys only in the base are kept. The UI uses the same merge.
  An absent overlay file leaves behaviour identical to today; an empty overlay file means no
  override; an overlay that is unreadable, not valid YAML or not a mapping is a configuration
  error naming the overlay file and never quoting its content. Relative paths in the merged
  configuration resolve from the base file's directory. No component ever writes the base file.
- R3: Every setting node of every shipped manifest's `settings_schema` (the root and each entry
  under `properties`, recursively, including `items` sub-schemas that have `properties`) carries
  a non-empty `title`; every setting node whose description documents a default as
  `Default <literal>.` — `<literal>` being a bare or backtick-quoted YAML value, or the word
  `empty` meaning the empty value of the node's type — carries a machine-readable `default`
  equal to that literal. The schema validator accepts `default` as an annotation with no
  constraint effect on validated values, and refuses — naming the schema path — a `default` that
  does not itself satisfy the node's schema. Descriptions remain as help text. A property
  literally named `default` inside `properties` stays a property, not an annotation.
- R4: The UI serves, all generated from manifests, the core's own declarations and the merged
  configuration, with no UI code naming any module:
  (a) a base page listing every discovered module (disabled ones included; discovered from the
  merged `modules_directory`) with its enabled state, its readiness, and a link to its page,
  plus the configuration's origin — base path, overlay path and whether it exists, and each
  referenced environment variable's name with set/unset state — and a local-only statement;
  (b) one page per discovered module rendering its `settings_schema` and, for a module whose
  manifest declares trigger types, its trigger policies per channel (combination operator and
  rules, each rule's parameters rendered from that type's `parameter_schema`);
  (c) a core-settings page rendering `modules_directory`, every `limits` group and field (from
  the core's limit declaration), and, read-only, the `secrets` block (each entry as its
  `${NAME}` reference with set/unset state) and the `actions` block (every rule with its
  `rule_id`, `action_name`, destination, principals, natures and granted permissions, with
  `${NAME}` references shown unresolved), each read-only block carrying the reason it cannot be
  edited in v1; the actions view also lists every action declared by an enabled module that no
  rule covers (no rule whose `action_name` equals it or is absent).
  Pages render the schema subset `type` (object, array, string, integer, number, boolean),
  `properties`, `required`, `additionalProperties` (false → no extra keys; a schema → named
  entries editor), `enum`, `items`, `minimum`, `maximum`. Each field shows its label, help text,
  documented default, current value, and origin (base / overlay / environment reference /
  default-not-set). A node the page cannot render as a control (object without `properties` nor
  schema `additionalProperties`, array without `items`, type `null`, the reserved module
  `limits` setting, a manifest without `settings_schema`) is shown with an explicit "not editable
  here" notice naming its setting path and, read-only, its unresolved configured text — never
  silently omitted. Readiness is the verdict of the last Check plus, when a status record exists
  (R7), the running process's per-module ready/degraded state with the record's time.
- R5: A Check action, on each module page, on the core-settings page and on the base page,
  validates a draft (the merged on-disk configuration plus the page's unsaved edits) without
  writing the overlay, without signalling or contacting the main process, and without opening a
  socket, through the existing check path (`core.main.check_config` with an injected `environ`).
  It reports every diagnostic it can in one run: all unresolved `${NAME}` references of the
  top-level blocks and of enabled modules (each by setting path and variable name), and — when
  none is unresolved — every diagnostic of the module-settings phase; when the settings phase
  could not be reached, the result says so explicitly. A module page shows the diagnostics that
  name that module; the base page shows all.
- R6: Save writes to the overlay only, and only within the 6c scope: `enabled_modules`,
  `modules.<name>.<setting path>`, `triggers.<input>.channels.<channel>` (a whole channel
  policy), `limits.<group>.<field>` and `modules_directory`. The UI refuses to write any file
  other than the managed overlay path resolved at startup, and refuses to start — with a
  diagnostic and a non-zero exit status, before binding anything — if that path equals the base
  path or if no overlay path can be resolved (a base name without an implicit overlay per A1 and
  no `--overlay`); it never serves pages without a writable overlay path. Any write whose target is `secrets`, `actions`, any other top-level key,
  a declared credential path, or a field whose configured value is a `${NAME}` reference is
  refused and changes nothing. Protection extends to ancestors: a write or remove-override whose
  target is an ancestor of a protected field (a declared credential path, or a field or mapping
  key whose configured text is a `${NAME}` reference — e.g. a whole object setting or a whole
  channel policy) is accepted only if every protected descendant keeps exactly its current
  configured text (a `${NAME}` reference stays that reference, a credential literal stays that
  literal, a referenced mapping key such as a trigger channel key stays that key); a write or
  removal that would change, replace or delete any protected descendant is refused and changes
  nothing. `secrets`/`actions` entries already present in the overlay are
  preserved unchanged by every write (A9). A write replaces the overlay atomically (a reader sees
  the old or the new file, never a partial one), is refused when the overlay changed on disk
  since the page was rendered (stale-write detection), and rejects values that violate the
  field's own contract — its schema node, a trigger type's `parameter_schema` or the module's
  supported combination operators, a limit's kind (positive integer or finite positive number),
  a non-empty `modules_directory` string, or an `enabled_modules` list of distinct discovered
  module names — returning per-field diagnostics and writing nothing. Remove-override deletes one
  key path from the overlay (pruning emptied parent mappings), after which the effective value is
  the base value again. Every accepted write or removal emits one log record with time, page,
  operation and the setting paths touched — never values.
- R7: Apply performs a supervised restart using a declared launch argv (given after `--` on the
  UI command line; default: the current interpreter running `core.main --config <base>` and the
  same `--overlay` if given), executed without a shell. Apply is refused unless a Check of the
  on-disk configuration passes. The UI stops the process it launched (terminate, bounded wait,
  then kill), starts the new one with the status-file variable set, and reports within a bounded
  window (documented default 60 s) one of: accepted (the new process's status record reports
  ready with a configuration digest equal to the on-disk digest), refused (the process exited;
  exit status and its value-free diagnostics shown), or unknown (window elapsed). When the
  status-file variable is set, the main process publishes the status record — replacing it
  atomically — when it becomes ready and again on **every** later module ready/degraded
  transition, each record containing its pid, start time, a sequence number strictly greater
  than the previous record's, a digest of the merged unresolved configuration document it
  loaded, and per-module state; never a resolved or credential value. Every page shows whether
  disk and the running process agree: in sync, differs, or unknown (no record, or the recorded
  pid is not alive); a process not launched by this UI is labelled "not supervised" and is never
  signalled. Status records are read only at the status path of A5.
- R8: No secret value appears in any HTML page, JSON response, UI log record, restart report, or
  diagnostic produced by the UI. Secret values are: the resolved value of every environment
  variable referenced anywhere in the base or overlay (as a value or as a mapping key), the
  values of variables listed in the `secrets` block, and any literal value at a declared
  credential path. Such fields are displayed only as their reference (`${NAME}`) or as "literal
  value configured (hidden)", with set/unset state. The access token appears only in the single
  startup line on standard output.
- R9: No runtime dependency is added: `[project].dependencies` stays exactly `aiohttp` and
  `PyYAML`. Every page works offline: no response makes the browser load, execute or submit to
  anything from another host — no external URL in any resource-loading or navigating position
  (`src`, `href`, form `action`, CSS `url(...)` or `@import`, script-initiated requests); all
  assets are served by the UI process itself. Configured values displayed per R4 are data: they
  may contain external URLs but are rendered only as HTML-escaped text (or JSON string values),
  never as a link, resource reference or markup.
- R10: `docs/config-ui.md` documents how to launch the UI; that it is local-only and does not
  administer a remote brain over the proxy; the overlay path rule (A1), the merge precedence (R2)
  and the fact that the overlay cannot delete a base key (A7); what the UI writes and that
  `secrets` and `actions` are read-only in v1 and why (6c); the secrets policy (R8); the
  restart/supervision and drift semantics (R7); and the status-file variable name and default
  status path (A5) with how to make an externally started main process visible to the UI.

## Acceptance criteria

- AC1 (R1): With `--host 0.0.0.0` (or `192.168.1.10`, or `::`) and no `--allow-non-loopback`,
  the UI exits with a non-zero status and a diagnostic, and a test proves no socket was bound;
  with `127.0.0.1`, `::1` or `localhost` the bind policy accepts; with `--allow-non-loopback` a
  non-loopback host is accepted and the token requirement still applies.
- AC2 (R1): Two UI starts produce two different tokens, each ≥ 22 URL-safe characters (≥ 128
  bits); the access URL containing the token is printed exactly once on standard output and
  appears in no log record.
- AC3 (R1): For each of the base page, the core-settings page, a module page, and each JSON
  endpoint, a request with no token/session returns 401 or 403 and a body containing none of the
  module names or setting paths; the same request with a valid session returns 200.
- AC4 (R1): A POST save with a valid session but a missing or wrong CSRF token returns 403 and
  leaves the overlay file's bytes unchanged; a GET to any save/remove/check/apply endpoint
  returns 405 and changes nothing.
- AC5 (R1): The whole UI test module runs with socket creation patched to raise, and passes.
- AC6 (R1): With bind `127.0.0.1:8765`, requests whose `Host` is `127.0.0.1:8765`,
  `localhost:8765`, `LOCALHOST:8765` or `[::1]:8765` are accepted, and `evil.example:8765`,
  `127.0.0.1:9999` or a missing `Host` get 403 (GET included); a POST with
  `Origin: http://localhost:8765` is accepted, and with `Origin: null`,
  `Origin: https://127.0.0.1:8765`, `Origin: http://evil.example:8765` or
  `Origin: http://127.0.0.1:8765/x` gets 403 with the overlay bytes unchanged. With bind
  `0.0.0.0:8765`, `--allow-non-loopback` and `--allowed-host box.lan`, `Host: box.lan:8765` is
  accepted, `Host: other.lan:8765` gets 403, and the printed URL uses `127.0.0.1:8765`.
- AC7 (R2): Given base `{a: {b: 1, c: [1, 2]}, d: x}` and overlay `{a: {b: 2, c: [3]}, e: y}`,
  the merged document is exactly `{a: {b: 2, c: [3]}, d: x, e: y}`; an overlay `a: null` yields
  `a: null` in the merged document.
- AC8 (R2): Overlay path derivation yields `config.local.yaml` for `config.yaml`,
  `presence.local.yaml` for `presence.yaml.example`, `x.local.yaml` for `x.yml`, none for
  `config.txt`, and the `--overlay PATH` value when given.
- AC9 (R2): For a profile whose overlay sets one enabled module's integer setting below its
  `minimum`, both `check_config` and `main(["--config", …, "--check-config"])` return 2 and name
  that module and field; deleting the overlay makes both return 0; `run` with a valid overlay
  value hands the module that overlay value; an overlay `limits.dedup.max_entries: 0` makes both
  return 2 naming `limits.dedup.max_entries`.
- AC10 (R2): An overlay whose content is `[1, 2]`, or invalid YAML containing the text
  `CANARY-OVERLAY`, makes `check_config` return 2 with a diagnostic naming the overlay file and
  not containing `CANARY-OVERLAY`; an existing empty overlay file gives the same result as no
  overlay. After any UI operation of AC22–AC28, the base file's bytes are unchanged.
- AC11 (R2): With no overlay file present, `.venv/bin/python -m pytest tests/ -q -p
  no:cacheprovider` reports ≥ 2,460 passed and ≤ 18 skipped plus the tests added here, with no
  failure outside the allowlist below.
- AC12 (R3): For all 17 shipped manifests, 100% of setting nodes (root, every `properties` entry
  recursively, and every `items` sub-schema having `properties`) have a non-empty string
  `title`; the test counts the nodes checked per manifest and the count is > 0 for each of the
  17.
- AC13 (R3): For every setting node whose `description` contains `Default <literal>.` as
  defined in R3, a `default` annotation is present and equal to the literal (e.g. audio_output
  `max_text_chars` → `400`; a `Default true.` node → `true`; a ``Default `alert`.`` node →
  `"alert"`; a ``Default `[delete_message]`.`` node → `["delete_message"]`; a `Default empty.`
  array node → `[]`); every declared `default` in the 17 manifests validates against its own
  node.
- AC14 (R3): `validate_schema` accepts `{type: integer, minimum: 1, default: 5}`, refuses
  `{type: integer, minimum: 1, default: 0}` and `{type: string, default: 3}` with a diagnostic
  naming `<label>.default`; validating the value `7` against a node with `default: 5` gives the
  same result as without the annotation; the audio_output `voices` schema still has exactly the
  two properties `allowed` and `default`.
- AC15 (R4): For each of the four shipped example profiles, the base page lists exactly the 17
  discovered modules, each with its enabled state matching `enabled_modules`, a link to its page
  (GET returns 200), the base path, the overlay path with exists/absent, each referenced
  variable name with set/unset, a link to the core-settings page (GET returns 200), and a
  local-only statement.
- AC16 (R4): Each of the 17 module pages renders a control for every renderable setting with its
  `title` as label, its description as help text, and its documented default; a test asserts,
  per module, that the set of setting paths shown (controls plus "not editable here" notices)
  equals the set of setting paths in the schema — 0 paths silently dropped — and that the
  reserved `limits` property appears as "not editable here".
- AC17 (R4): A fixture module added only as a new directory with its `module.yaml` (not among
  the 17) gets a working page with its titled fields and no UI code change; a static test
  asserts that no UI source file contains any of the 17 module names as a string literal.
- AC18 (R4): A fixture schema containing an object without `properties`, an array without
  `items` and a `null`-typed field renders each with a "not editable here" notice naming its
  setting path; an `enum` field offers exactly its members; an integer field with `minimum: 1,
  maximum: 10` exposes both bounds; a `required` field is marked required.
- AC19 (R4): With `config.yaml.example`, the twitch module page shows the policy of channel
  `${TWITCH_BROADCASTER_ID}` (key shown as that reference) with combination `all_of` and one
  `keyword` rule whose `keywords` parameter is `["!ask"]`, and offers exactly the rule types the
  twitch manifest declares (`probability`, `audience`, `keyword`); a module whose manifest
  declares no trigger types shows no trigger section.
- AC20 (R4): The core-settings page for `config.yaml.example` shows `modules_directory`, every
  `limits` group and field present in the core's limit declaration (a test compares the two
  sets: 0 missing, 0 extra) with its current value, and shows the `secrets` block and all rules
  of the `actions` block (the count of rules shown equals the count in the merged
  configuration) with no input control, no save control and a visible reason that they are
  read-only in v1. With a fixture where an enabled module declares action `x.do` and no rule has
  `action_name: x.do` or omits `action_name`, `x.do` is listed as not covered; adding such a
  rule removes it from that list.
- AC21 (R4): Given a fixture module with settings `a`, `b`, `c`, `d` (each with `default: 5`),
  base `modules.M: {a: 1, c: "${VAR_C}", d: 5}` and overlay `modules.M: {a: 2}`, the module page
  shows: `a` value `2` with origin overlay; `c` origin environment reference showing `${VAR_C}`
  and its set/unset state, never the resolved value; `d` value `5` with origin base; `b` origin
  default-not-set with default `5` and no configured value. Removing the override on `a` changes
  its origin to base with value `1`. With a status record reporting module M `degraded`, M's
  readiness on the base page shows degraded with the record time; with no record, readiness
  shows only the last Check verdict and states that no running process is known.
- AC22 (R5): A draft with 3 unresolved `${NAME}` references across two enabled modules reports
  3 diagnostics (each with setting path and variable name) in one Check and states that the
  settings phase was not reached; a draft with 0 unresolved references and 2 invalid settings
  in two enabled modules reports both diagnostics in one Check; a module page's Check lists only
  the diagnostics naming that module, the base page's Check lists all of them.
- AC38 (R5): With an on-disk configuration that passes Check, a draft editing one enabled
  module's integer setting to a value below its `minimum` (unsaved) makes that Check fail with a
  diagnostic naming that module and setting; conversely, with an on-disk overlay setting that
  field below its `minimum` (on-disk Check fails), a draft editing it back to a valid value
  (unsaved) makes Check pass. In both cases the overlay file's bytes are unchanged afterwards.
- AC23 (R5): During Check the overlay file's bytes and mtime are unchanged, no process is
  signalled or started, and no socket is created (socket creation patched to raise).
- AC24 (R6): Each of these saves writes an overlay containing exactly that override (plus
  previously existing overrides) and a subsequent `load_config` yields the new value: setting S
  of module M → `modules.M.S`; disabling one module on the base page → `enabled_modules` equal to
  the base list minus that module; `limits.dedup.max_entries: 2048`; `modules_directory:
  ./other` (and the base page then lists the modules discovered there); a new twitch channel
  policy `triggers.twitch.channels.chan2` with one `probability` rule of `0.5`. Each accepted
  save emits one log record listing the setting paths touched and containing no value.
- AC25 (R6): Remove-override on `modules.M.S` makes the effective value equal the base value
  again and removes `modules.M` (and `modules` if empty) from the overlay; for a trigger channel
  policy defined in the base file, the page offers no delete action and states that the overlay
  cannot delete a base entry.
- AC26 (R6): Each of these saves returns a per-field diagnostic and leaves the overlay bytes
  unchanged: an out-of-range or wrong-typed module setting; `limits.dedup.max_entries: 0` and
  `limits.dedup.ttl_seconds: "x"`; an empty `modules_directory`; an `enabled_modules` list with
  a duplicate or an undiscovered name; a trigger rule of a type the module does not declare, a
  `probability` of `1.5`, or a combination operator the module does not support. A save after
  the overlay was modified on disk since the page render is refused as stale and writes nothing.
- AC27 (R6): Attempts to write the `secrets` block, the `actions` block (adding, changing or
  removing a rule), any other top-level key outside the 6c scope, a declared credential field,
  or a field whose configured value is a `${NAME}` reference are refused and change no file; an
  overlay hand-written with an `actions` rule keeps that rule byte-for-byte equivalent (same
  parsed value) after an unrelated module-setting save; starting the UI with `--overlay` equal
  to the base path exits non-zero, and starting it with `--config config.txt` and no `--overlay`
  exits non-zero with a diagnostic naming the missing overlay path, before binding (socket
  creation patched to raise); the same `config.txt` with `--overlay other.local.yaml` starts.
  Ancestor protection: with a fixture module whose object setting `o` holds `{k: "${VAR_K}", n:
  1}`, a save of `modules.M.o` as `{k: "${VAR_K}", n: 2}` is accepted and the overlay keeps
  `k: "${VAR_K}"`, while a save of `modules.M.o` as `{k: "plain", n: 2}` or `{n: 2}` (dropping
  `k`) is refused and changes no file; with an object setting containing a declared credential
  path holding a literal in the overlay, a remove-override of that object is refused and changes
  no file; saving the whole twitch channel policy keyed `${TWITCH_BROADCASTER_ID}` with a changed
  rule keeps that key as the reference text in the overlay, never its resolved value.
- AC28 (R6): A write interrupted after the new content is prepared but before it replaces the
  overlay (simulated failure) leaves the previous overlay content intact and parseable.
- AC29 (R7): With a failing on-disk Check, Apply is refused and no process is stopped or
  started.
- AC30 (R7): With a fake launch argv whose child writes a status record reporting ready and the
  current on-disk digest, Apply reports accepted; with a child exiting 2 after printing a
  value-free diagnostic, Apply reports refused with exit status 2 and that diagnostic; with a
  child writing nothing within a window shortened to 1 s, Apply reports unknown. The launch argv
  is executed without a shell (an argv element `;echo x` is passed literally).
- AC31 (R7): Stopping the supervised child sends terminate, then kill after the bounded wait
  when the child ignores terminate; a record at the UI's status path written by a live process
  whose pid is not a child of this UI makes the pages show that process's per-module state
  labelled "not supervised", and Apply never signals that pid. The default status path for base
  `config.yaml` is `config.yaml.status.json` in the same directory, `--status-file PATH`
  overrides it, and a record at any other path is not reported.
- AC32 (R7): Starting from a status record written by a live process whose digest equals the
  on-disk configuration digest, every page shows "in sync"; after an overlay save that changes
  the effective configuration (a module setting set to a value different from its current one),
  not yet applied, every page shows "differs"; after a successful Apply it shows "in sync" again;
  with no status record, or with a record whose pid is not alive, it shows "unknown".
- AC33 (R7): `run` with the status-file variable set and two fixture modules M and N: when the
  process reports ready, the record exists with state ready, M ready, N ready, pid, start time,
  digest and sequence `s1`; when M's health owner then reports degraded, the record is replaced
  and shows M degraded, N ready and a sequence `s2 > s1`; when M reports ready again, the record
  shows M ready with `s3 > s2` — 3 transitions, 3 distinct published records, each observed in
  order. `run` without the variable writes no file.
- AC34 (R7): A record replacement interrupted after the new content is prepared but before it
  replaces the file (simulated failure) leaves the previous record intact and parseable; a
  reader repeatedly parsing the status path during the 3 transitions of AC33 never parses a
  partial record.
- AC35 (R8): With environment canaries (e.g. `CANARY-ENV-7f3a`) for every referenced variable
  (including those in `secrets` and those used as mapping keys such as the trigger channel key)
  and a literal canary at a declared credential path, a test fetches the base page, the
  core-settings page, all 17 module pages, every JSON endpoint, performs a Check, a save, a
  removal and an Apply (accepted and refused), reads every status record of AC33, collects all
  response bodies, headers, log records and restart reports, and asserts that 0 of them contain
  any canary; credential fields display `${NAME}` or "literal value configured (hidden)".
- AC36 (R9): `[project].dependencies` lists exactly `aiohttp` and `PyYAML`; `[project.scripts]`
  contains the existing `twitch-ia-compagnon` entry plus exactly one entry targeting the UI's
  main. For every UI response, no `src`, `href` or form `action` attribute value, CSS `url(...)`
  or `@import` target, and no URL in served script code points to a host other than an accepted
  authority (A8). With a non-secret string setting configured as
  `https://cdn.example.invalid/x.js"><script>` the module page shows that text HTML-escaped (the
  literal `&lt;script&gt;` appears, no new element is created) and the text appears in no
  `src`/`href`/`action` attribute.
- AC37 (R10): `docs/config-ui.md` exists and contains the launch command `python -m
  core.config_ui`, the word "local-only" (or "local only"), the overlay path rule with the two
  examples of AC8, the merge precedence of AC7, the statement that the overlay cannot delete a
  base key, the list of the 5 writable blocks and the 2 read-only blocks (`secrets`, `actions`)
  with the reason, the secrets policy, the three drift states (in sync, differs, unknown), and
  the status-file variable name with the default status path rule of A5.

## Test failure allowlist

- `tests/test_users.py::test_manifest_is_v2_and_declares_users_read_without_granting_it` — it
  asserts `schema["properties"]["max_channels"]` equals exactly `{type, minimum, description}`;
  R3 requires a `title` on that node, so the exact-dict assertion contradicts R3. The adaptation
  keeps the `type: integer` and `minimum: 1` checks.
- `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`
  — it asserts `[project.scripts]` equals exactly the one `twitch-ia-compagnon` entry; R1 (and
  AC36) require a second console entry for the UI, so the exact-dict assertion contradicts R1.
  The adaptation keeps the `core.main:main` entry, the build-backend and the dependency
  assertions unchanged.

## Caller Enumeration

API changes: `core.main.load_config`, `core.main.check_config` and `core.main.run` gain overlay
support (an optional overlay-path keyword; existing positional/keyword usage keeps working with
the implicit A1 path), `core.main.main` gains `--overlay`, `core.main` exposes its limit-group
declaration publicly (the private table stays the single source used by `_validate_limits`), and
`core.contracts.validate_schema` accepts the `default` annotation. Search method:
`grep -rn "load_config(\|check_config(\|await run(\|validate_schema(\|_LIMITS" --include=*.py
core modules tests`; blind spot: calls through aliases or `getattr`.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/main.py | `run`, `check_config`, `main` → `load_config` | Pass the overlay path through; no change for callers that omit it |
| core/main.py | `_validate_limits` → limit-group table | Keeps reading the same table the new public declaration exposes |
| core/config_ui/ (new) | Check path → `check_config`; limits page → limit declaration | Calls with the draft and injected `environ` |
| tests/test_main.py (29 call sites) | `run`, `check_config`, `load_config` | None; tmp_path profiles have no overlay file |
| tests/test_profiles.py (7) | `check_config`, `load_config` | None |
| tests/test_examples.py (5) | `load_config`, `check_config` | None; the repository has no `*.local.yaml` (gitignored) |
| tests/test_integration.py (2) | `run` | None |
| tests/test_shutdown.py (2) | `run` | None |
| core/contracts.py (6 internal) | `validate_schema` recursion | Carry the `default` check through nested nodes |
| core/loader.py (1) | `validate_schema` on `settings_schema` | None; accepts the new annotation |
