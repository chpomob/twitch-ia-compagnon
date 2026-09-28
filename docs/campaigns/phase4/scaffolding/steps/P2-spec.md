# Step P2 — Shared overlay module

Plan step `P2` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Create `core/overlay.py` as the one implementation shared by the runtime and the UI. It names no module and imports only the stdlib and `yaml`. It provides:
  - `STATUS_FILE_VARIABLE = "TWITCH_IA_COMPAGNON_STATUS_FILE"` (D4).
  - `OverlayError(ConfigurationError-compatible message)`. Define it as a plain `RuntimeError` subclass carrying a value-free message, so `core.main` can map it to `ConfigurationError` without importing cycles.
  - `implicit_overlay_path(base: Path) -> Path | None` per A1:
    - Strip a trailing `.example`.
    - Then replace a final `.yaml`/`.yml` with `.local.yaml` in the same directory.
    - Any other name returns `None`.
  - `resolve_overlay_path(base, explicit) -> Path | None`: the explicit `--overlay` wins, otherwise the implicit path.
  - `read_overlay(path) -> Mapping`:
    - An absent file returns `{}`.
    - An empty file or a YAML `null` document returns `{}`.
    - A file that is unreadable, not valid YAML, or not a mapping raises `OverlayError` with `"overlay file <path>: is not readable" / "is not valid YAML" / "must be a mapping"`. The message never quotes the content or the parser excerpt; catch `yaml.YAMLError` and re-raise `from None`.
  - `deep_merge(base, overlay) -> dict` per R2:
    - A mapping merges key by key, recursively.
    - Any other overlay value (scalar, list, null) replaces the base value wholesale.
    - Keys that exist only in the base are kept.
    - Inputs are never mutated; the result is a fresh deep copy.
  - `read_base(path) -> Mapping`: the same parsing `load_config` does today, with the same messages, so both callers share it.
  - `canonical_digest(document) -> str`: SHA-256 of `json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)`.
    - Non-string mapping keys are converted to `str()` before sorting, through a pre-pass, because `sort_keys` fails on mixed key types.
    - The result is lowercase hex.
  - `on_disk_digest(base, overlay_path) -> str` = `canonical_digest(deep_merge(read_base(base), read_overlay(overlay_path)))`.
  - `same_file(a, b) -> bool`: compares `os.path.realpath(os.path.abspath(x))` of both paths, which follows symbolic links whether or not the files exist.
  - `status_path_collision(status, base, overlay) -> str | None`: returns `"base"` / `"overlay"` / `None`. It uses the same resolved comparison as `same_file`.
  - `default_status_path(base) -> Path`: the base file's full name with `.status.json` appended (A5).

## Requirements

Plan mapping: R2, R7
Acceptance criteria owned by this step: AC7, AC8
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

[`core/overlay.py`, `tests/test_overlay.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

Create `tests/test_overlay.py` covering:
  - AC7: the exact merged document, `a: null` replacing, and the input mappings left unmutated.
  - AC8: `config.yaml` → `config.local.yaml`, `presence.yaml.example` → `presence.local.yaml`, `x.yml` → `x.local.yaml`, `config.txt` → `None`, an explicit path winning.
  - `read_overlay`:
    - An absent file and an empty file give `{}`.
    - `[1, 2]` → OverlayError naming the path.
    - Invalid YAML containing `CANARY-OVERLAY` → a message without the canary.
  - `canonical_digest`:
    - Stable under key order.
    - Non-ASCII written verbatim, and hashes the literal UTF-8 bytes.
    - A YAML date and an integer key are stringified.
    - Always 64 lowercase hex characters.
  - `status_path_collision` returns base or overlay for:
    - a path equal to the file;
    - a relative non-normalised spelling (`./sub/../config.local.yaml` with `sub/` present) under `monkeypatch.chdir`;
    - a symlink to the base;
    - a not-yet-existing overlay path.
  - A different name gives `None`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- `realpath` on a non-existent path whose parent is a symlink still resolves the parent, which is desired. Test it.
  - `default=str` must not hide mappings with non-string keys. The pre-pass converts keys recursively, and the digest must be identical between the runtime (P4) and the UI (P14), which is guaranteed because both call this function.
  - An import cycle `core.main` ↔ `core.overlay`: `core.overlay` must not import `core.main`.

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
