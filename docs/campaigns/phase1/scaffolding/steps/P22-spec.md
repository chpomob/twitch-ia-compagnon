# Step P22 — Two-process topology test over loopback (R6, R7; AC39)

Plan step `P22` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Spawn two `python -m core.main --config <tmp>` processes with `asyncio.create_subprocess_exec`: the brain with a temp profile derived from `config.server.yaml.example` (twitch replaced by `fakeplatform` with a `feed` firing on `agent.status`, TLS disabled on `127.0.0.1`, `endpoint` pointing at a fake OpenAI-compatible `aiohttp.web` server run in the test process that answers the probes and the scripted scenario, audit output to a temp file, `modules_directory` a temp copy of shipped + fixture modules — decision 7) and the agent with a temp profile derived from `agent.yaml.example` (`ws://127.0.0.1:<port>`, two capture sources: `default_source: file` (a PNG written by the test) for the end-to-end scenarios, and `gated`, a `command` source whose argv runs a helper script the test writes into the temp dir with `command_timeout_seconds: 60`). **Capture synchronisation contract for the kill scenario:** the test opens a loopback TCP "gate" server (`asyncio.start_server` on `127.0.0.1`, port passed to the helper through argv); the helper, when started by the agent's capture provider, connects to the gate (that connection is the *capture-start acknowledgement* the test awaits with `wait_for`), then blocks reading one byte from the gate; on a byte it writes the PNG to stdout and exits 0, on EOF it exits 1. The test therefore holds the capture in flight for as long as it wants, kills the agent only after the acknowledgement, and releases the orphaned helper by closing the gate connection afterwards (the helper never outlives the test). The scripted model asks for `screen.capture {"source": "gated"}` in that scenario and `{"source": "file"}` (or no argument) in the others. The test observes readiness through each process's stdout `ready` line, model requests through the fake server, pairing through the agent's `agent.status` event recorded in the brain's audit file, and capture start through the gate — all with bounded `asyncio.wait_for`, never a sleep. Scenarios: valid token pairs; invalid token → close 4401 (agent stderr diagnostic + audit trace); AC1 end to end with the image transferred by reference (the audit file shows the `attachment_id`, never a path); the gated scenario: await the gate acknowledgement, then `SIGKILL` the agent while that `screen.capture` is in flight → the run's observation in the audit file is `error proxy_disconnected` (and the brain's `agent.status`/readiness shows `screen.capture` not ready), then close the gate connection; restart → pairs again, `screen.capture` ready again and a second scenario succeeds; both processes exit 0 on `SIGTERM`.

## Requirements

Plan mapping: R6, R7
Acceptance criteria owned by this step: AC39
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

[`tests/test_proxy_process.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P21]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_proxy_process.py` as described; marks itself `skip` naming the reason when a free loopback port cannot be bound. The gate helper is asserted deterministic: the acknowledgement arrives before the kill in every run (the test fails, rather than passing vacuously, if the capture completed — a PNG on the helper's stdout — before the kill).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (process boundary, wall-clock). The brain process runs on real time: keep budgets in the temp profile generous (`total_run_seconds: 60`) and every wait bounded (`wait_for(..., 30)`); the fake model server must respond to the probe before the brain's startup deadline. The gated helper's `command_timeout_seconds` (60) must exceed every `wait_for` bound so the agent's own timeout never races the kill; the orphaned helper is released through the gate (EOF) in a `finally` block so a failing test leaves no child behind.

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
