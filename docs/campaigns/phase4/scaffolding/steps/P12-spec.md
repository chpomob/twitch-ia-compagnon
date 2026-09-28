# Step P12 — Check

Plan step `P12` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial (module boundary into `core.main`). Adds `POST /check` from the base, core and module pages:
  - The draft overlay is the on-disk overlay with the posted unsaved edits applied, using the same path-setting routine Save uses, factored as `_apply_edits(overlay, edits)`.
  - **Unresolved-reference phase:** the draft is merged over the base and scanned for `${NAME}` references in the top-level blocks (excluding `modules` and `secrets`) and in the settings of *enabled* modules (by `enabled_modules` of the merged draft). Every reference whose variable is absent from the UI environ is reported as `<setting path>: ${NAME} is unresolved` (a variable name is not a secret). If any is reported, the result also says "settings phase not reached" and `check_config` is not called.
  - **Settings phase:** otherwise, the default checker calls `_run_socket_free(check_config(base, environ=ui_environ, overlay=overlay_path, overlay_document=draft, diagnostic_reporter=collect))` (D7, defined in P9) and collects every diagnostic. It never calls `asyncio.run`, so no self-pipe `socketpair` is created. Before running, it refuses with `RuntimeError("Check must not run on a running event loop")` if its thread already has a running loop (D8 guard); in production it always runs on a `config-ui` worker thread reached through `_dispatch`. An injectable `checker` seam exists for the AC22 unit cases, but AC38, AC23 and the bridge test use the **real** default checker.
  - **Module pages** show the diagnostics naming that module: the text contains `modules.<name>.` or starts with `<name>:` / `module <name>`, via a shared `_diagnostic_names_module` predicate. The base page shows all.
  - The last Check verdict is stored per UI for readiness (R4).
  - Diagnostics pass through the redaction guard.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC22, AC38, AC23
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

[P3, P11]

All dependencies are already merged into `main` when this step runs.

## Tests

- **AC22:**
    - 3 unresolved references across two enabled fixture modules → 3 diagnostics with path and variable, plus "settings phase not reached".
    - 0 unresolved and 2 invalid settings in two modules → both diagnostics in one Check.
    - A module page lists only its own; the base page lists all.
  - **AC38:** a passing on-disk config plus a draft below `minimum` → a failure naming the module and setting; a failing on-disk overlay plus a draft fixing it → pass; the overlay bytes are unchanged in both cases.
  - **AC23:** the overlay bytes and mtime are unchanged, `subprocess.Popen` and `os.kill` are monkeypatched to fail if called, and the socket patch stays active.
  - AC38 and AC23 run the real default checker (no seam) under P9's socket-creation patch, and assert the fixture's socket-creation counter is 0 afterwards: Check creates no socket at all, not merely none bound or connected.
  - **D7 pin:** `asyncio.SelectorEventLoop` defines `_make_self_pipe`, `_close_self_pipe` and `_write_to_self`; `_run_socket_free` of a coroutine that awaits `asyncio.sleep(0)`, a `loop.run_in_executor` call (a cross-thread `call_soon_threadsafe` wake-up) and an `asyncio.wait_for` with a timeout completes, closes the loop, and creates no socket.
  - **Bridge (D8), part 2 — the real adapter-to-checker path (findings P2):** on `_SocketFreeEventLoop` (standing in for the aiohttp serving loop, which the tests cannot bind), `await _dispatch(ui, <authenticated POST /check with a valid CSRF token>, executor)` with the real checker returns the AC38 verdict (a pass for a valid draft, the module/setting failure for an invalid one). This proves that Check runs from inside a running loop's handler without the "cannot be called from a running event loop" failure. A companion test calls the default checker directly from a coroutine on a running loop and asserts the D8 `RuntimeError`, proving the guard.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- D7 relies on three private CPython `BaseSelectorEventLoop` methods. The pin test above fails loudly on an interpreter that changes them; the fallback would be the same override under the new names, never re-enabling the socketpair. Flagged in the P19 review summary.
  - The 10 ms selector cap makes the Check loop poll. Check is short-lived (bounded by `core.main`'s startup deadline), so the cost is negligible, and it is the price of having no self-pipe.
  - `check_config` may start the runtime's collaborators; any of them that creates a socket at assembly time would now be caught by the patch. `check_config`'s own docstring (AC40 of an earlier phase) states assembly opens nothing, so a failure here is a real bug to report, not a patch to relax.
  - Diagnostic-to-module attribution is text-based and may miss a module-wide diagnostic phrased differently. Enumerate the diagnostic shapes from `core/loader.py` (`_field_diagnostic`, `_module_diagnostic`) and base the predicate on them.

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
