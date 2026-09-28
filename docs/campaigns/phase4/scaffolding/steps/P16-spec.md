# Step P16 — Secret-canary sweep and offline-assets audit (R8, R9; AC35, AC36 URLs, AC2 log)

Plan step `P16` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- Add the end-to-end canary test:
    - a fixture environment gives every variable referenced in the 4 example profiles (including `secrets` entries and the trigger channel key variable) a value `CANARY-ENV-<hex>-<name>`;
    - a fixture overlay puts a literal canary at a declared credential path of an enabled module;
    - a `caplog`/handler captures every `core.config_ui` record.
  - The test drives, through `ConfigUI.handle`:
    - the base page, the core page, all 17 module pages and every JSON endpoint;
    - one Check, one Save, one Remove;
    - an Apply accepted and an Apply refused, using the P15 fake children;
    - and it reads every AC33 status record, re-running P4's publisher with the canary env.
  - It asserts that 0 bodies, headers, log records, restart reports or status records contain any canary, and that credential fields show `${NAME}` or "literal value configured (hidden)".
  - Add the AC36 URL audit: parse every response with `html.parser`; every `src`/`href`/`action` value and every CSS `url(`/`@import` target is relative or points to an accepted authority; the inline script contains no `http://`/`https://` literal to another host.
  - Fix any leak found in `__init__.py`; this is the only reason this step may touch production code.

## Requirements

Plan mapping: R8, R9
Acceptance criteria owned by this step: AC35, AC36, AC2
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

[`tests/test_config_ui.py`, `core/config_ui/__init__.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P15]

All dependencies are already merged into `main` when this step runs.

## Tests

AC35 and AC36 (attribute and CSS URL audit) as described; AC2's "token in no log record" re-checked over the whole sweep's log capture.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- A canary that is a substring of page chrome is impossible by construction (random hex).
  - The publisher's records hold no resolved values, since the digest is over unresolved text; the check proves it.
  - If a leak is found, fix the renderer rather than relying only on the redaction guard.

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
