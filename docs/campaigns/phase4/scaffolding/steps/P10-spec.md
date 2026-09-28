# Step P10 — UI configuration model, base page and core-settings page (R4 a/c, R8, R9, A2, A3, A9; AC15, AC20, AC3 pages)

Plan step `P10` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial (module boundary: loader discovery; secret policy).
  - **Configuration model:** `ConfigView.load(settings, environ)` builds a snapshot:
    - reads the base (`read_base`) and the overlay (`read_overlay`) raw, and merges them (`deep_merge`);
    - records the overlay's bytes digest and mtime for stale detection (used in P13);
    - resolves `modules_directory` (merged value; `builtin` → `core.main._builtin_modules_directory()`; relative paths from the base directory);
    - discovers modules through `ModuleLoader(EventBus(), dir)._discover()` (D6);
    - collects every referenced `${NAME}` (as a value or as a mapping key), anywhere in base and overlay, with set/unset state from the UI's own environ (A3);
    - computes the **secret set**: the resolved values of those variables, the values of the variables named in `secrets`, and the literals at every declared credential path of every discovered module;
    - computes the **origin** of any setting path: `overlay` if the path exists in the overlay, else `base` if it exists in the base, else `default-not-set`. The value is shown as an `environment reference` when its configured text is `${NAME}`.
  - **Rendering helpers:**
    - `esc()` is used for every configured value, via `html.escape(quote=True)`;
    - a credential or reference value is displayed as `${NAME}` or "literal value configured (hidden)";
    - a final **redaction guard** `_redact(body)` scans every outgoing body, header, log record and report for each secret-set value (length ≥ 1) and replaces it with `[hidden]`. This is defence in depth; the renderers never place values from the secret set in the first place.
    - All CSS and JS are inline constants, forms post to relative paths, and there is no external URL anywhere (R9).
  - **Base page `/`** (R4a):
    - every discovered module (disabled ones included), each with an enabled toggle form (P13 wires the save), its readiness cell (last Check verdict, plus the status-record state from P14 once available), and a link `/module/<name>`;
    - the configuration origin: the base path, the overlay path with exists/absent, and each referenced variable with set/unset;
    - a link `/core`;
    - a local-only statement;
    - a Check button.
  - **Core-settings page `/core`** (R4c):
    - `modules_directory` (editable);
    - every group and field of `core.main.LIMIT_DECLARATION` with its kind and current value (editable);
    - `secrets` read-only: each entry as `${NAME}` with set/unset, and origin overlay when hand-written (A9);
    - `actions` read-only: every rule's `rule_id`, `action_name` (or "any"), destination, principals, natures and granted permissions, with `${NAME}` references left unresolved;
    - both read-only blocks carry the reason "read-only in v1: editing could widen the authorized actions / expose secrets";
    - an "actions not covered by any rule" list: actions declared by *enabled* modules for which no rule has an equal `action_name` or omits it.
  - The UI source never names a module (A2, R4).

## Requirements

Plan mapping: R4, R8, R9
Acceptance criteria owned by this step: AC15, AC20, AC3
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-4 decisions that override any contrary reading of a requirement:
- The configuration UI runs in a **separate process** (`python -m core.config_ui`); it is a
  **local-only** surface (loopback by default, an access token, a Host/Origin and CSRF guard),
  never a remote administration channel, and it adds **no runtime dependency** beyond aiohttp
  and PyYAML, with no CDN or external asset.
- Its writing scope (decision 6c) is exactly `enabled_modules`, `modules.<name>`, `triggers`,
  `limits` and `modules_directory`. The `secrets` block and the `actions` block (the default-deny
  authorization rules) are **read-only in v1 and displayed read-only**, so a single mis-click can
  never widen the set of authorized actions.
- Writes go to a **managed overlay file** merged over the untouched base file (comments preserved);
  a secret VALUE never appears in any page, response, log or diagnostic, only its reference and its
  set/unset state.
- Every module page is **generated from the module manifest** (schema plus presentation metadata);
  **no module ships HTML**, and adding a module must add its page with no UI code change.
- Applying a change is a **supervised restart** (there is no hot reload in this phase).

## Files

[`core/config_ui/__init__.py`, `tests/test_config_ui.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3, P9]

All dependencies are already merged into `main` when this step runs.

## Tests

- **AC15:** for each of the 4 `*.example` profiles, rendered with a fixture environ, the base page lists exactly the 17 discovered modules with enabled states matching `enabled_modules`; each link returns 200 (P11 makes module pages real; P10 asserts 200 via the placeholder, and P11 re-asserts the content). It shows the base path, the overlay path with absent, the referenced variables with set/unset, a `/core` link returning 200, and the local-only statement.
  - **AC20:** the `/core` page of `config.yaml.example`:
    - limit groups and fields match `LIMIT_DECLARATION` exactly (0 missing, 0 extra);
    - the rule count shown equals the merged `actions` count;
    - no `<input>`, `<select>`, `<textarea>` or save form inside the secrets and actions sections, and the reason text is present;
    - with a fixture module declaring action `x.do`, `x.do` is listed as not covered, and adding a covering rule removes it.
  - **AC3 pages:** with the real pages, an unauthenticated GET of `/` and `/core` contains no module name.
  - **A9:** a hand-written overlay `actions` rule appears with origin overlay.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- `_discover()` raises `ModuleLoadError` when one manifest is invalid. The UI must still render the base page with the diagnostic (value-free) instead of crashing.
  - Redacting very short secret values (e.g. `"1"`) would mangle pages. The guard redacts only values ≥ 4 characters. Shorter secrets are still never rendered because the renderers show references only. The minimum length is documented in the code.
  - Reference keys used as mapping keys (the trigger channel key) must be collected as references.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase4): ...`, `fix(phase4): ...`, `test(phase4): ...`, `refactor(phase4): ...`, `docs(phase4): ...`).
  The scope MUST be `phase4`: the repository history is full of `feat(phase3):` / `fix(phase2):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase4)`.
- Keep the phase-0 to phase-3 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, and the phase-3 moderation/memory/watch semantics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
