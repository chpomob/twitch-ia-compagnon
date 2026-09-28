# Step P4 — Status-record publisher in `run`, with collision refusal (R7, A5; AC33, AC34, AC41 runtime half, AC43)

Plan step `P4` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial (ordering, atomicity, process boundary). In `core/main.py`, add a private `_StatusPublisher`:
  - **Construction** takes the status path, `started_at` (taken once at the very top of `run`, before `load_config`, as a UTC timestamp string with millisecond precision and a `Z` suffix), the digest (`core.overlay.canonical_digest` of the *merged unresolved* document the process loaded), the enabled module names, and an injectable writer seam for tests.
  - **Obtaining the digest:** `load_config` gains an internal helper `_load_documents(...) -> (merged_unresolved, config)`. `run` uses it to obtain the merged document and the resolved config without reading the files twice. The public `load_config` signature stays as in P3.
  - **`publish()`** writes one JSON object:
    - fields `version: 1`, `pid: os.getpid()`, `started_at`, `published_at` (now, same format), `sequence` (starts at 1 and adds 1 per publish), `digest`, `state: "ready"`, and `modules: {name: {"state": ...}}` for exactly the enabled modules;
    - each module's state is its last `ready`/`degraded` transition: the publisher reads `runtime.context.health.state(name)` for each enabled module at publish time and maps `stopped`/`None` to `degraded` (a decision flagged in a code comment; after readiness every enabled module has reported `ready`, because the coordinator reports readiness only after every `prepare` succeeded);
    - the write is atomic: write to a `NamedTemporaryFile` in the same directory, `flush` + `os.fsync`, then `os.replace`. On any failure the temp file is removed and the previous record is left intact.
  - **Wiring in `run`:**
    - Read `environ.get(STATUS_FILE_VARIABLE)` (the injected `environ` or `os.environ`).
    - When it is set, *before* `load_config` or any other effect, check `status_path_collision(status, base, resolve_overlay_path(base, overlay))`. On a collision, call `report_diagnostic("status_path_collision: status file collides with the <base|overlay> file <path>")` and return 2. Nothing has been opened for writing and no module has started.
    - After `report_ready("ready")`, publish once.
    - Subscribe on `runtime.bus` to `module.ready` and `module.degraded` (D5). The handler publishes a new record when an enabled module's state changes after readiness. It is wrapped so a publish failure reports a value-free diagnostic (`status record: could not be written`) and never stops the runtime.
    - Without the variable, the publisher is never constructed and no file is written.
  - Record content never includes resolved values, only the digest of the *unresolved* document.

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC33, AC34, AC41, AC43
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

[`core/main.py`, `tests/test_status_record.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2, P3]

All dependencies are already merged into `main` when this step runs.

## Tests

Create `tests/test_status_record.py`, using tmp_path fixture modules M, N (and P for AC43) whose entry points expose a hook to drive `context.health.degraded/ready`:
  - **AC33 (publisher side):**
    - Three transitions produce three records, observed in order.
    - Checks per record: `version == 1`, `pid == os.getpid()`, the same `started_at`, a `published_at` timestamp, `sequence` 1 < s2 < s3, `digest == core.overlay.on_disk_digest(base, overlay)`, `state == "ready"`, keys `{M, N}`.
    - Each record also passes the reader classifier. That assertion is added in P14, when the classifier exists, as a follow-up test in `tests/test_status_record.py` listed in P14.
  - **AC34:**
    - Patch `os.replace` to raise on the second publish, then assert that the first record is intact and parseable and that no temp file is left.
    - A reader thread-free check: wrap the writer seam so that, before each `os.replace`, the test parses the current file. It must always be a complete object.
  - **AC41 runtime half:** the variable set to the base path, to the implicit overlay path while that file is absent, and to a relative spelling of an explicit `--overlay` path each give a non-zero exit and a diagnostic containing `status_path_collision`. No fixture module was activated, the base and overlay files are byte- and mtime-identical, and no overlay file was created.
  - **AC43:** the base enables M, N, P and the overlay `[M, N]`. The first record has keys `{M, N}` and the digest of that base plus overlay. Then the overlay is rewritten on disk; N degrades; the next record keeps keys `{M, N}` with N degraded and the same `started_at` and `digest`, with a greater sequence. A run enabling no module publishes `modules: {}`.
  - Without the variable, no file is created in tmp_path.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- Bus subscription semantics: the trace may be published through supervision rather than as a bus event in some runtimes. Verify by reading `Supervision.record_and_emit` first. If the traces are not bus events, fall back to wrapping `runtime.context.health.observe_phase`/`_transition` from `main.py` only, and flag it.
  - A publish on a transition during shutdown (`stopped`) must not publish a `stopped` module state: only ready/degraded changes trigger a publish.
  - `fsync` on tmp filesystems is fine.
  - The `started_at` must be taken before anything else so AC43's "same `started_at`" holds.

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
