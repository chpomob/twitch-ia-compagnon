# Step P9 — UI core

Plan step `P9` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial (security boundary, branching). Creates the socket-free core (D2, D3).
  - **`UISettings.from_argv(argv)`** parses:
    - `--config` (required), `--overlay`, `--host` (default `127.0.0.1`), `--port` (default `8765`), `--allow-non-loopback`, the repeatable `--allowed-host`, and `--status-file`;
    - the launch argv after `--`, split off before argparse.
  - **`startup_checks(settings) -> list[str]`** is pure and runs before any bind:
    - non-loopback host without `--allow-non-loopback`, judged via `ipaddress` (`localhost` counts as loopback; `0.0.0.0`/`::` are non-loopback);
    - no resolvable overlay path (A1), with a diagnostic naming the base and saying that `--overlay` is required;
    - overlay equal to base (`same_file`);
    - `status_path_collision` for the explicit or derived status path, with the diagnostic `status_path_collision: … <base|overlay> <path>`, never substituting another path.
  - **`accepted_authorities(host, port, allowed_hosts) -> frozenset[str]`** per A8: lower-cased, IPv6 in brackets, port always explicit in the stored form. The printed URL uses `127.0.0.1` for a wildcard bind.
  - **`ConfigUI`** holds:
    - the token: `secrets.token_urlsafe(32)`, at least 43 characters and 256 bits;
    - the session table: a session cookie `HttpOnly; SameSite=Strict; Path=/`, set on `GET /?token=…` followed by a redirect to `/`;
    - a per-session CSRF token (`secrets.token_urlsafe(32)`), compared with `hmac.compare_digest`;
    - `handle(UIRequest) -> UIResponse`, which runs the guards in this order:
      1. a missing `Host` or one outside the accepted set → 403;
      2. no valid session → 401 with a generic body, no module names or paths;
      3. a state-changing path (`/save`, `/remove`, `/check`, `/apply`) with a non-POST method → 405;
      4. on POST, an `Origin` check (absent → allowed; `null`, another scheme, a path, or an unaccepted authority → 403; `http://h` ≡ `http://h:80`);
      5. a CSRF mismatch → 403.
    - A route table dispatches to page and endpoint handlers. P9 installs placeholders that P10–P15 replace; the base page placeholder already returns 200 so AC3 can run.
  - **`main(argv) -> int`:**
    - `startup_checks`; on failure, print the diagnostics to stderr and return 2 before creating anything;
    - otherwise build `ConfigUI`, print exactly one line `Configuration UI: http://<authority>/?token=<token>` to stdout, then `serve()` (the aiohttp adapter: `web.Application` with one catch-all handler, `web.run_app` on host and port with `print=None`).
  - **Request bridge (D8):** the catch-all handler reads the body, builds a `UIRequest` with the pure `_to_ui_request(method, path, query, headers, body)`, then `await _dispatch(ui, ui_request, executor)`, which runs `ui.handle` in the dedicated `config-ui` thread pool via `run_in_executor`, and converts the `UIResponse` back. `handle` is never called on the serving loop. `ConfigUI` gains the mutation lock and the session-table lock described in D8.
  - **Socket-free loop (D7):** define `_CappedSelector`, `_SocketFreeEventLoop` and `_run_socket_free(coro)` here, in their own delimited section, so both the bridge test (below) and Check (P12) use the one implementation.
  - The UI's logger (`core.config_ui`) never receives the token.
  - `__main__.py` calls `raise SystemExit(main())`.

## Requirements

Plan mapping: R1, R6
Acceptance criteria owned by this step: AC1, AC6, AC27, AC41
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

[`core/config_ui/__init__.py`, `core/config_ui/__main__.py`, `tests/test_config_ui.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2]

All dependencies are already merged into `main` when this step runs.

## Tests

Create `tests/test_config_ui.py` with an autouse module-scoped fixture (AC5) that patches **socket creation** to raise, exactly as R5/AC5/AC23 state: `socket.socket.__init__` (every Python-level socket object, including the ones `socket.socketpair`, `socket.fromfd`, `socket.create_connection` and `socket.create_server` construct), plus `socket.socketpair`, `socket.fromfd`, `socket.create_connection` and `socket.create_server` themselves, each raising `AssertionError("socket created")` and counting the attempt. Nothing is left unpatched for asyncio: Check uses the socket-free loop of D7 (P12). A fixture self-test proves the patch is effective: `socket.socket()`, `socket.socketpair()` and `asyncio.new_event_loop()` (which needs the self-pipe) all raise inside the module, and the attempt counter is checked to be 0 at module teardown for every test that is not the self-test. Tests:
  - **AC1:** `main(["--config", p, "--host", "0.0.0.0"])`, `192.168.1.10` and `::` return non-zero with a diagnostic, and the patched socket was not called (the patch raises, and `serve` is also monkeypatched to fail the test if reached). `127.0.0.1`, `::1` and `localhost` pass `startup_checks`. With `--allow-non-loopback` a non-loopback host passes and unauthenticated requests still get 401.
  - **AC2:** two `ConfigUI` instances have different tokens of at least 22 URL-safe characters. With `serve` monkeypatched to a no-op, `main` prints the URL exactly once, captured with `capsys`, and `caplog` holds no token.
  - **AC3:** every route without a session returns 401/403 with no module name or setting path in the body. The pages are re-checked in P10/P11 once they exist.
  - **AC4:** a POST without or with a wrong CSRF token → 403 with the overlay bytes unchanged; GET on `/save`, `/remove`, `/check`, `/apply` → 405.
  - **AC6:** the full Host/Origin matrix, including the `0.0.0.0` + `--allowed-host box.lan` case and the printed URL `127.0.0.1:8765`.
  - **AC27 startup:** `--overlay` equal to base → non-zero; `config.txt` without `--overlay` → non-zero, naming the missing overlay path; with `--overlay other.local.yaml` the checks pass.
  - **AC41 UI half:** `--status-file` equal to the base, the overlay, `./sub/../config.local.yaml`, a symlink to the base, and no `--status-file` with `--overlay config.yaml.status.json` each exit non-zero with `status_path_collision`, with no bind, no Popen (monkeypatched to fail), and files byte- and mtime-identical. A non-colliding `--status-file` passes.
  - **Bridge (D8), part 1:** `_to_ui_request` maps method, path, query, headers (lower-cased names) and body exactly; `_dispatch` run on `_SocketFreeEventLoop` (introduced here as a helper for this test; the Check use is P12) returns the same `UIResponse` as a direct `handle` call and runs `handle` on a thread whose name starts with `config-ui`, not the loop thread. No socket is created (the fixture counter stays 0). P12 extends this test to a `/check` request with the real checker.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- The UI test module contains no pytest-asyncio tests (D2): every coroutine it runs goes through `_run_socket_free`, so no fixture-provided loop tries to create a socketpair under the patch.
  - `_SocketFreeEventLoop` and `_run_socket_free` are introduced in P9 (for the bridge test) and reused by P12; defining them once avoids two loop variants.
  - Patching `socket.socket.__init__` module-wide also affects any library the UI imports lazily; if one creates a socket at import time, the test fails loudly, which is the intent.
  - A cookie session over plain http on a LAN bind is sniffable. Accepted: local-only is the documented model (R10).
  - `localhost` resolution must never use DNS; the check is by name only.
  - The argparse `--` split: argparse treats `--` specially, so split `argv` at the first `--` manually before parsing.

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
