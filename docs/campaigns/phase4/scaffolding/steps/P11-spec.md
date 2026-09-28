# Step P11 — Generated module pages

Plan step `P11` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial (branching schema renderer). Adds `/module/<name>`, which renders the module's `settings_schema` recursively over the subset `type`, `properties`, `required`, `additionalProperties`, `enum`, `items`, `minimum` and `maximum`:
  - **Controls:**
    - string → text input;
    - integer/number → number input with `min`/`max` and step 1 for integer;
    - boolean → checkbox;
    - enum → select with exactly its members;
    - array with `items` → a list editor (JSON text area validated server-side);
    - object with `properties` → a fieldset recursion;
    - object with a schema `additionalProperties` → a named-entries editor;
    - `additionalProperties: false` → no extra-key control.
  - **Each field shows:** its `title` label, the description as help text, the documented default, the current value, its origin (P10), a required marker, and a remove-override control only when the origin is overlay.
  - **"Not editable here" notices** name the setting path and show the unresolved configured text read-only (never a secret value: credential paths show "literal value configured (hidden)"). They cover:
    - an object without `properties` and without a schema `additionalProperties`;
    - an array without `items`;
    - type `null`;
    - the reserved `limits` property;
    - a manifest without `settings_schema`.
  - **Trigger section:** rendered only when the manifest declares trigger types.
    - Per configured channel of `triggers.<module>.channels`, it shows the combination operator (select limited to the module's supported operators) and its rules, each rule's parameters rendered from that type's `parameter_schema` with the same renderer.
    - A channel key that is a `${NAME}` reference is shown as that reference text.
    - An "add channel policy" form offers exactly the declared rule types.
    - A channel defined in the base shows no delete action and the note "the overlay cannot delete a base entry" (A7).
  - Every configured value is escaped text, never an attribute URL (R9).

## Requirements

Plan mapping: R4, R4
Acceptance criteria owned by this step: AC16, AC19, AC21, AC36
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

[P8, P10]

All dependencies are already merged into `main` when this step runs.

## Tests

- **AC16:** for each of the 17 modules, the set of setting paths shown (controls plus notices, collected from `data-path` attributes) equals the schema's path set, with `limits` shown as "not editable here"; titles appear as labels and defaults are shown.
  - **AC17:** a fixture module added only as a directory with `module.yaml` gets a page with its titled fields. A static test reads `core/config_ui/*.py` and asserts that none of the 17 module names appears as a string literal (via `ast` string constants).
  - **AC18:** a fixture schema with an object without `properties`, an array without `items` and a `null` field gives three notices naming their paths; an enum offers exactly its members; `min="1" max="10"` is present; the required marker is shown.
  - **AC19:** the twitch page with `config.yaml.example` shows channel `${TWITCH_BROADCASTER_ID}`, `all_of`, one `keyword` rule with `["!ask"]`, and rule types exactly `{probability, audience, keyword}`; a module without trigger types shows no trigger section.
  - **AC21 (value/origin part):** fixture M with base `{a:1, c:"${VAR_C}", d:5}` and overlay `{a:2}` shows `a` = 2 (overlay), `c` as `${VAR_C}` with set/unset and never the value, `d` = 5 (base), and `b` default-not-set with default 5.
  - **AC36 escaping:** the value `https://cdn.example.invalid/x.js"><script>` appears as `&lt;script&gt;` and appears in no `src`/`href`/`action` attribute.
  - **AC3:** an unauthenticated module page → 401 without the module name.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- Setting-path identity for AC16: paths through `items` and `additionalProperties` entries need a stable notation (`a.b[]`, `a.<entry>`); define it once and use it both in the renderer and in the test's schema walker.
  - Deeply nested brain schemas could produce huge pages; acceptable.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase4): ...`, `fix(phase4): ...`, `test(phase4): ...`, `refactor(phase4): ...`, `docs(phase4): ...`).
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
