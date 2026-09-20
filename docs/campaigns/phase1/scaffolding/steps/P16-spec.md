# Step P16 — `modules/capture`: `screen.capture` from `file` and `command` sources into the run-leased store (R5, R4; AC29, AC23, AC30)

Plan step `P16` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Manifest v2 (roles `[]`, settings `sources: {<name>: {kind: file, path} | {kind: command, argv[], command_timeout_seconds (default 5)}}`, `default_source`, `max_bytes` (default 5 242 880); validator checks shapes, finite positive numbers, non-empty argv, no value echoed; `actions: [screen.capture]`: read, permission `screen.capture`, destinations `*/*/capture`, args `{source?: string}`, result `{source, content_type, width, height, size, captured_at}`, `timeout_seconds: 10`, `idempotency: none`). `prepare` checks each source (file readable; command executable resolved on PATH or absolute) and on failure reports `degraded` naming the source and does not mark ready; otherwise registers the provider and marks ready. The provider (with an injectable `source_factory` seam and `subprocess` runner for tests — P7's `FakeCaptureSource`) reads the file afresh or runs argv with `asyncio.create_subprocess_exec` bounded by `command_timeout_seconds` (kill on expiry → `error capture_timed_out`, 0 bytes stored), refuses bytes above `max_bytes` (`error attachment_refused`), sniffs PNG/JPEG headers for `content_type`, `width`, `height` (PNG IHDR; JPEG SOF markers), stores through `context.attachments.put(run_id, data, content_type=...)` (an `AttachmentRefused` → `error attachment_refused`, 0 bytes retained), and returns `success` with the result mapping and one `image_ref` part (`attachment_id`, `content_type`, `size`, `width`, `height`, `captured_at`, `provider_id: "capture"`). No local path in any result, part or trace.

## Requirements

Plan mapping: R5, R4
Acceptance criteria owned by this step: AC29, AC23, AC30
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

[`modules/capture/__init__.py`, `modules/capture/module.yaml`, `tests/test_capture.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P3, P9]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_capture.py`: AC29 (file source → `image_ref` matching the file's `width/height/size`; command source exceeding the timeout → `capture_timed_out`, store 0 bytes; missing source at `prepare` → not ready, `module.degraded` names the source); AC23 (`max_object_bytes: 1024`, 2048-byte capture → `attachment_refused`, 0 bytes retained); AC30 (no rule → refused, provider 0 invocations); an unknown `source` argument → `invalid_arguments`; the observation contains no filesystem path; `command` runs a Python one-liner writing a PNG to stdout (no sleep in the test — the timeout case uses the injected runner held on a future).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (process boundary). The timeout test must not spawn a sleeping child: the runner seam is held on a future and the clock is advanced. Header sniffing must reject truncated headers as `invalid_result`-shaped provider errors rather than raising.

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
