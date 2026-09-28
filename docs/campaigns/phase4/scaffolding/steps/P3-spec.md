# Step P3 — Runtime merges the overlay; `--overlay` CLI; public limit declaration (R2; AC9, AC10)

Plan step `P3` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial (API change, branching). Changes to `core/main.py`:
  - **`load_config(config_path, *, environ=None, overlay=None, overlay_document=None)`:**
    - Read the base via `core.overlay.read_base`, which keeps today's exact diagnostics.
    - Resolve the overlay path with `resolve_overlay_path(base, overlay)`. With no path (A1 non-yaml base and no `--overlay`), read the base alone.
    - Read the overlay via `read_overlay`, unless `overlay_document` is given (D1: an in-memory draft used instead of the file). Map `OverlayError` to `ConfigurationError(str(exc))`.
    - `deep_merge` before `${NAME}` resolution and validation.
    - Relative `modules_directory` still resolves from the base file's directory, because `path.parent` is unchanged.
    - Nothing ever writes either file.
  - **`check_config(..., overlay=None, overlay_document=None)` and `run(..., overlay=None)`** pass these through to `load_config`.
  - **`main()`** gains `--overlay PATH` (optional), forwarded to both `check_config` and `run`.
  - **`LIMIT_DECLARATION`:** a public read-only view of the limit-group table, `MappingProxyType` of `MappingProxyType`s (group → field → `"count"`/`"seconds"`), with the kind constants exported as `LIMIT_KIND_COUNT`/`LIMIT_KIND_SECONDS`. `_validate_limits` keeps reading `_LIMITS`, which stays the single source.

  Caller table (from `grep -rn "load_config(\|check_config(\|await run(\|validate_schema(\|_LIMITS" --include=*.py core modules tests`; blind spot: calls through aliases or `getattr`):

  | File | Function/Method | Migration Note |
  |------|----------------|----------------|
  | core/main.py | `run` → `load_config` | Passes `overlay`; default `None` = implicit A1 path |
  | core/main.py | `check_config` → `load_config` | Passes `overlay` and `overlay_document` |
  | core/main.py | `main` → `check_config`, `run` | Forwards `--overlay` |
  | core/main.py | `_validate_limits` → `_LIMITS` | Unchanged; `LIMIT_DECLARATION` wraps the same object |
  | core/config_ui/__init__.py (P10, P12) | `check_config(base, environ=…, overlay=…, overlay_document=draft)`; `LIMIT_DECLARATION` | New callers |
  | tests/test_main.py (29 sites) | `run`, `check_config`, `load_config` | None: tmp_path profiles have no `*.local.yaml` |
  | tests/test_profiles.py (7) | `check_config`, `load_config` | None |
  | tests/test_examples.py (5) | `load_config`, `check_config` | None; `.gitignore` (P17) keeps `*.local.yaml` out of the repo. A developer's stray `config.local.yaml` beside an example would now be merged. This is documented in P18 |
  | tests/test_integration.py (2), tests/test_shutdown.py (2) | `run` | None |
  | core/contracts.py (6 internal) / core/loader.py (1) | `validate_schema` | Covered by P1 |

## Requirements

Plan mapping: R2
Acceptance criteria owned by this step: AC9, AC10
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

[`core/main.py`, `tests/test_overlay.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_overlay.py`, using tmp_path fixture modules:
  - **AC9:**
    - An overlay setting an enabled fixture module's integer below `minimum` makes both `check_config` and `main([... "--check-config"])` return 2, naming the module and the field. Deleting the overlay gives 0.
    - `run` with a valid overlay value hands the module that value; a fixture module records its settings, with an injected `stop_event` and `ready_reporter`.
    - `limits.dedup.max_entries: 0` in the overlay gives 2, naming `limits.dedup.max_entries`.
    - `--overlay PATH` is honoured by `main`.
  - **AC10:** `[1, 2]` and invalid YAML with `CANARY-OVERLAY` return 2, naming the overlay file without the canary. An empty overlay equals no overlay.
  - An `overlay_document` argument overrides the file.
  - `LIMIT_DECLARATION` equals `_LIMITS` by content and is immutable.
  - Run the full `tests/test_main.py`, `tests/test_profiles.py` and `tests/test_examples.py` unchanged.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- Any test that writes a `*.local.yaml` next to a tmp base now changes behaviour. Grep tests for `local.yaml` first.
  - Today's exact error strings (`"configuration file: is not readable"` and similar) are asserted in `tests/test_main.py`, so `read_base` must reproduce them byte-for-byte.
  - Merging before resolution means an overlay may introduce `${NAME}` references, which are resolved normally, as intended.

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
