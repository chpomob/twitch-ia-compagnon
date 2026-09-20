# Step P21 — Deployment profiles, example tests and profile suite (R7, R8; AC40, AC41, AC42, AC43, AC58)

Plan step `P21` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`config.yaml.example` becomes the PC profile: `enabled_modules: [twitch, chat_context, users, capture, brain, audit]`, settings for `chat_context` (`{}`), `users` (bounds), `capture` (`sources: {screen: {kind: command, argv: [...]}}` with a documented placeholder command and `max_bytes`), brain block per P9, rules granting exactly `chat.read`, `users.read` (scope `chat`), `screen.capture` (scope `capture`), `chat.write` to principal `brain` on `${TWITCH_BROADCASTER_ID}`, 0 literal secrets. `config.server.yaml.example`: same minus `capture`, plus `proxy` (`listen: {host: 0.0.0.0, port: 8765}`, `tls: {certfile: ${PROXY_TLS_CERT}, keyfile: ${PROXY_TLS_KEY}}`, `pairing_token: ${PROXY_PAIRING_TOKEN}`, `actions: [screen.capture]`), same four grants, same single-entry `delivery`, `secrets` listing the token. `agent.yaml.example`: `enabled_modules: [capture, agent_link]`, `agent_link: {brain_url: ${BRAIN_URL}, pairing_token: ${PROXY_PAIRING_TOKEN}, agent_id: pc-agent, actions: [screen.capture], reconnect, call_table}`, one rule granting `screen.capture` to principal `brain` on `*/*/capture`, `limits` with `attachments` bounds, 0 secrets. `tests/test_examples.py` (already extended by P17 with the `_activate_example` seams — this step builds on that edit, hence the dependency): `MODULE_NAMES`/`EXPECTED_MANIFESTS`/`DECLARATION_KEYS` extended to the 8 shipped manifests (docstring updated, no assertion weakened); the two allowlisted tests rewritten: PC profile enables the six modules, and for each of the three profiles the granted action-name set equals the provided-action set (enabled manifests' actions ∪ `modules.proxy.actions` when `proxy` is enabled) — PC and server `{chat.read, users.read, screen.capture, chat.write}` with `screen.capture` from the proxy allowlist only in the server file, agent `{screen.capture}`; AC42 budgets equal `(5, 30, 10, 8192, 5242880, 6, 2, 10, 30, 60)`; every credential a `${NAME}` reference; AC58 `delivery` group equality on both brain profiles; every module-owned validator accepts its section. `tests/test_profiles.py`: AC40 (`--check-config` exits 0 on each of the three files with dummy environment and 0 sockets — `socket.socket` patched to count; `TWITCH_ACCESS_TOKEN` unset → 2 naming the path, no value); transport selection (server profile with `proxy` → `screen.capture` bound to the proxy provider; PC profile → to `capture`; both → ambiguity diagnostic); AC43 (one command, `<sys.executable> -m pip install --no-deps --no-build-isolation --target <empty temp dir> <checkout>`, network-free because the backend is imported from the test environment; then a subprocess run from a directory outside the checkout with `PYTHONPATH=<target>` and an empty `sys.path` entry for the checkout, discovering exactly the 8 manifests `twitch, brain, audit, chat_context, users, capture, proxy, agent_link` through `modules_directory: builtin`, and `<target>/bin/twitch-ia-compagnon --help` (run through the interpreter from the installed console-script entry point when the launcher is not on PATH) exiting 0). The test skips **only** when `pip` is unavailable (`<sys.executable> -m pip --version` fails), naming that reason; any other failure — build backend not importable, install error, wrong manifest count — is a test failure, never a skip (AC43).

## Requirements

Plan mapping: R7, R8
Acceptance criteria owned by this step: AC40, AC41, AC42, AC43, AC58
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.
Phase-1 product decision that overrides any contrary reading: delivery is a **configured, pluggable
terminal step** — a configured ordered list of delivery actions (mode `fixed`, or mode `modules`
derived from the enabled modules that declare a delivery capability, with a configurable preference
order), each entry declaring how the answer text maps into its arguments (a named argument, or none
for an effect-only delivery such as a stream-scene change); every entry runs at the terminal step
only, each receiving the text only if declared; the model never selects the delivery and never
performs an intermediate effect; adding a delivery module must require no change to the agentic loop.

## Files

[`config.yaml.example`, `config.server.yaml.example`, `agent.yaml.example`, `tests/test_examples.py`, `tests/test_profiles.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P5, P6, P13, P14, P15, P16, P17, P19, P20]

All dependencies are already merged into `main` when this step runs.

## Tests

As described: `tests/test_examples.py` and `tests/test_profiles.py`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (irreversible-ish: the shipped profiles are what users copy). `--check-config` on the agent profile with a dummy `BRAIN_URL` must pass the `wss`/loopback rule — the example uses `wss://brain.example:8765`. The install test depends on `pip` and on the build backend being importable in the test environment (`--no-build-isolation`, so nothing is downloaded); P6 declares `setuptools>=69` in the `test` extra for that reason, and an environment lacking it fails the test with a diagnostic naming the extra — AC43 forbids any skip other than `pip` unavailable.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase1): ...`, `fix(phase1): ...`, `test(phase1): ...`, `refactor(phase1): ...`).
- Keep the phase-0 guarantees in force: bounded admission, phase lifecycle, explicit terminal action
  outcomes, default-deny authorization (reads included), bounded retention, a single global
  startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss diagnostics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- No new runtime dependency beyond the existing ones unless this step is the one that adds it; no
  model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
