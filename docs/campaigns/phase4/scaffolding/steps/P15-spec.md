# Step P15 — Apply

Plan step `P15` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial (process boundary, signals, bounded waits). Adds `Supervisor` and `POST /apply`:
  - **Launch argv:** the argv given after `--`, or by default `[sys.executable, "-m", "core.main", "--config", <base>]` plus `["--overlay", <overlay>]` when `--overlay` was given.
  - **Apply sequence:**
    1. Run an on-disk Check (P12 with no edits). On failure, refuse with its diagnostics; nothing is stopped or started.
    2. Stop the child *this UI launched*, if any: `terminate()`, then `wait(timeout=stop_grace)` (default 10 s, injectable), then `kill()` and wait. A record's foreign pid is never signalled (A4).
    3. Start `Popen(argv, shell=False, env={**os.environ, STATUS_FILE_VARIABLE: status_path}, stdout=None, stderr=PIPE, start_new_session=False)` — stdout inherited, so it can never fill a pipe (D9) — and immediately start the child's `_StderrDrain` thread (below) before polling.
    4. Poll within the window (default 60 s, `apply_window` injectable) using an injected `clock` and `wait` (the real `wait` = `threading.Event().wait(interval)`, never `time.sleep`; tests inject a fake clock):
       - **accepted:** a usable status record whose `pid == child.pid`, `state == ready` and `digest ==` the on-disk digest;
       - **refused:** `child.poll()` is not `None`; join the drain thread (bounded, 2 s, so EOF has been read), then report the exit status and the last 2 KiB of the drained stderr tail, passed through the redaction guard and value-free;
       - **unknown:** the window elapsed. An unusable record never counts.
  - **`_StderrDrain` (D9):** a daemon `threading.Thread` per child that loops `os.read(fd, 4096)` on the stderr pipe until EOF, writes each chunk to the UI's `sys.stderr.buffer` (a write failure is ignored for forwarding only; draining never stops early), and appends it to a bounded tail `bytearray` trimmed to the last 8 KiB under a lock. `tail(limit)` returns the decoded (`errors="replace"`) last `limit` bytes. It runs for the child's whole life, including after an accepted Apply, so a chatty runtime never blocks on a full pipe. Stopping a child (step 2 of a later Apply, or UI shutdown) waits for the process, then joins its drain with a bounded timeout; the pipe is closed after the join.
  - The restart report is shown on the page and logged (paths and outcome only).
  - `/apply` requires POST + CSRF (P9).

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC29, AC30, AC31, AC32, AC39, AC40
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

[P13, P14]

All dependencies are already merged into `main` when this step runs.

## Tests

Fake child scripts written to tmp_path and run with `sys.executable`; each writes the status record via `core.overlay` helpers.
  - **AC29:** a failing on-disk Check → refused; the `Popen` spy was never called and the existing child was not terminated.
  - **AC30:**
    - a child writing a ready record with its own pid and the on-disk digest → accepted;
    - a child exiting 2 after printing a value-free line → refused with status 2 and that line;
    - a child writing nothing within a 1 s window → unknown;
    - an argv element `;echo x` reaches the child literally (the child echoes `sys.argv` into a file).
  - **AC31:** a child ignoring SIGTERM (`signal.signal(SIGTERM, SIG_IGN)`) is killed after the bounded wait, recorded via a spy on `terminate`/`kill` and on the order of calls; a foreign-pid record → `os.kill` spy never called with that pid.
  - **AC32 apply:** after a successful Apply, pages show "in sync".
  - **Pipe backpressure (findings P4):**
    - a child that writes 1 MiB to stderr and 1 MiB to stdout (each far above the 64 KiB Linux pipe capacity) *before* writing its ready record → accepted within the 1 s window, which would be `unknown` if stderr were not drained;
    - a child that, after its ready record, keeps writing 1 MiB to stderr and then writes a marker file → after accepted, the marker file appears within a bounded poll (`Event.wait` loop ≤ 5 s), proving draining continues after acceptance;
    - a child that writes 1 MiB of filler then the line `LAST-LINE` to stderr and exits 2 → refused with status 2, the report contains `LAST-LINE`, and the report's stderr part is ≤ 2 KiB;
    - `_StderrDrain` unit test on an `os.pipe()` pair (no socket): 100 KiB written, tail length stays ≤ 8 KiB and ends with the last bytes written.
  - **AC39/AC40 apply legs:** a child writing only an unusable record, V with `state: "starting"`, or V with another pid → unknown after a 1 s window, never accepted.
  - The waits use a real bounded `Event.wait` with a short interval. The hygiene sleep check forbids `sleep` calls only, and `Event.wait` is not one. Confirm with `test_ac45_the_suite_makes_no_positive_duration_sleep_call`'s implementation before relying on this.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- Real child processes make tests slower and potentially flaky. Keep the windows at ~1 s and the children trivial.
  - `Popen` itself does not create sockets, so the AC5 patch holds.
  - Zombie children: always `wait()` after a kill.
  - The child's stderr may contain a secret if a child misbehaves. Pass the reported tail through the redaction guard and cap it at 2 KiB. Forwarding to the UI's own stderr is equivalent to the operator running the runtime directly (the runtime's diagnostics are value-free by contract), and it goes to the terminal, never into a page or the UI's log records.
  - Forwarded child output lands in pytest's fd capture during tests; that capture is a temp file and cannot back-pressure.
  - A drain thread blocked on a pipe whose write end a grandchild still holds could outlive the child; the join is bounded and the thread is a daemon, so the UI never hangs on it.
  - The spec's shutdown watchdog in `core.main` bounds how long terminate takes. The default `stop_grace` of 10 s exceeds the core shutdown deadline, so the value is aligned in the doc.

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
