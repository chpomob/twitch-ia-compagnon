# Brief — Phase 4: a local web configuration UI, with one page per module

Project: `twitch-ia-compagnon` (repo `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`).
Status before this phase: phases 0–3 delivered, 17 modules, 2,460 unit tests green,
`main` at the phase 3 closing gate (`docs/campaigns/phase3/scaffolding/campaign-summary.md`).

## Problem

Configuring the companion today means hand-writing a ~500-line `config.yaml` (the shipped
`presence.yaml.example` is 583 lines) plus 25 distinct environment variables, then discovering
errors **one at a time** from `--check-config`, which prints nothing at all when it succeeds.
The operator-facing knowledge lives only in the example files' comments and in the test suite
(`tests/test_profiles.py`, `tests/test_examples.py`). There is no way to see what is enabled,
what is bound, what is not ready and why, or which actions still lack an authorization rule.

The operator wants a **small local web UI** for configuration, where **every module exposes its
own configuration page** reachable from a base page, rather than one monolithic YAML file.

## Decisions already made by the operator (verbatim choices, binding)

- **1b — the UI lives in a separate process**: `python -m core.config_ui` (a new console entry
  point), not a module inside the brain's process. It reads and writes the configuration files
  and can restart the main process.
- **2a — writing goes to an overlay**: a separate `config.local.yaml` merged over the base
  configuration file, so the heavily commented example stays intact and readable.
- **3a — secrets are never edited in v1**: pages show a secret's *reference* (`${NAME}`) and its
  set/unset state only. No secret value may ever appear in any page, JSON response, log line or
  diagnostic.
- **4a — applying a change is a supervised restart** driven from the UI (no hot reload).
- **5a — every module page is generated from the module's declared JSON-Schema subset.** Modules
  ship no HTML.
- **6b — v1 scope**: read + validate + a base page + one page per module + write + restart.
- **6c — the v1 writing scope, decided after the first spec round (amends 6b, binding)**: the UI
  **writes** `enabled_modules`, `modules.<name>` settings, `triggers`, `limits` and
  `modules_directory`. The `secrets` block and the `actions` block (the default-deny
  authorization rules) are **read-only in v1 and displayed read-only**, so that a single
  mis-click can never widen the set of authorized actions. A specification that quietly narrows
  the writing scope below 6c, or that widens it into `actions`, is wrong.

## Requirements (to be refined by the spec)

- **R-UI-1** A separate process serves the UI on a **loopback address only** by default, with a
  generated access token; it must refuse to bind a non-loopback address unless explicitly
  configured, and it must be testable with zero sockets (see machine facts).
- **R-UI-2** A **base page** lists every discovered module with its enabled state and its
  readiness, plus a link to each module's own page, plus the effective configuration's origin
  (base file, overlay, environment).
- **R-UI-3** **One page per module, generated from the manifest**: the module's manifest declares
  everything the page needs (`settings_schema` plus the new presentation metadata); no module
  ships HTML, and adding a module adds its page with **no UI code change**.
- **R-UI-4** Pages must render exactly the schema subset the project already validates — `type`
  (object, array, string, integer, number, boolean), `properties`, `required`,
  `additionalProperties`, `enum`, `items`, `minimum`, `maximum` — and must state honestly what
  they cannot render rather than silently dropping a setting.
- **R-UI-5** Presentation metadata the pages need (at minimum a human label per field and its
  documented default) must become **machine-readable in the manifest**, not prose: today 17
  manifests carry **0 `title` keys** and a single `default`. The prose descriptions stay as help
  text.
- **R-UI-6** A **Vérifier/Check** action per page and globally: it validates a **draft**
  configuration without touching the running process, reusing the existing check path
  (`core.main.check_config`, which accepts an injected `environ` and opens no socket), and
  reports **every** diagnostic it can, not the first one.
- **R-UI-7** **Writing**: the UI manages an overlay file only; it must never rewrite the base
  example, must deep-merge the overlay over the base with documented precedence, must let an
  override be removed (restoring the base value), and `--check-config` / the runtime must
  understand the overlay identically.
- **R-UI-8** **Applying**: the UI offers a supervised restart of the main process using a
  declared launch command, reports whether the new configuration was accepted after restart, and
  always shows whether the running process's configuration matches what is on disk. The status
  record must be published on **every** transition — when the process becomes ready and on each
  module's ready/degraded change — and acceptance criteria must cover those transitions, not only
  the creation of the record.
- **R-UI-11** The `actions` block is displayed read-only, with the reason it cannot be edited in
  v1, so the operator can see which authorizations exist without being able to change them.
- **R-UI-9** **Security**: no secret value in any response or log; per-session token; CSRF
  protection on writes; the UI may write only the managed overlay path; every write is logged.
- **R-UI-10** **No new runtime dependency** beyond aiohttp and PyYAML; the UI must work fully
  offline (no CDN, no external asset).

## Machine facts the implementation must respect (measured on `main`, 2026-09-27)

1. The project's schema validator lives in `core/contracts.py` (`validate_schema` at line 705,
   `validate_against_schema` at 779) and supports only the subset in R-UI-4.
2. Manifests are validated strictly: unknown top-level keys and unknown action keys are
   **refused** (`core/loader.py`, e.g. `_ACTION_KEYS` at line 1731). Adding a manifest key means
   extending the loader's allowlists **and** `tests/test_examples.py` (1,611 lines), which pins
   the exact allowed key set per module.
3. `core.main.check_config(config_path, *, environ=..., diagnostic_reporter=...)` returns 0/2,
   activates no module and opens no socket; configuration-file loading raises on the **first**
   unresolved `${NAME}` while the module-settings phase collects several diagnostics.
4. **There is no reload path**: `core/admission.py` states a closed scheduler cannot be
   restarted, `core/runtime.py` forbids a module reconfiguring another, and
   `modules/moderation` notes pending state is lost on restart. Apply therefore means restart.
5. The precedent for an HTTP surface is `modules/proxy`: aiohttp, explicit `listen` host/port,
   optional TLS, and an `_is_loopback(host)` discipline already under test.
6. The operator's environment variables number 25 across the four shipped examples (16 for the
   presence profile), including non-secrets such as the audio player argv and the memory
   directory.
7. Inventory available to the UI: 17 modules, 14 declared actions, four shipped example profiles
   (`config.yaml.example`, `config.server.yaml.example`, `agent.yaml.example`,
   `presence.yaml.example`).
8. Test command: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (project virtualenv;
   the system python lacks `aiohttp`). The suite must not drop below **2,460 passed, 18 skipped**.
9. Deployment has two profiles: everything on the streaming PC, or a brain on a remote host with
   a PC-side agent over the proxy. Remote administration is **out of scope** for this phase; the
   UI is local-only and must say so.

## Explicitly out of scope for this phase

- Editing secret values (3a).
- Hot reload without restart (4a).
- Administering a remote brain over the proxy.
- Module-supplied custom HTML pages.
- Any generated configuration wizard/migration tool.

## Exit criteria

1. `python -m core.config_ui` serves the base page and one page per module on loopback, with a
   token; a test proves no page, response or log carries a secret value.
2. Every one of the 17 modules gets a usable page with labels and defaults, generated from its
   manifest, without UI code that names a module.
3. Check reports all diagnostics for a draft configuration without touching the running process.
4. An overlay write, removal of an override, and a documented merge precedence are covered by
   tests, and the runtime/`--check-config` honour the overlay.
5. The apply path is honest: it restarts with a declared command and tells the operator whether
   the new configuration was accepted, and whether disk and the running process agree.
6. The full suite stays green (≥2,460 passed), no new runtime dependency is introduced, and the
   UI works offline.
