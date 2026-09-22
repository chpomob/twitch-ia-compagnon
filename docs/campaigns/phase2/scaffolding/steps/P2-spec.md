# Step P2 — Executor validates and releases `audio_ref` leases exactly as `image_ref` (R4; AC14)

Plan step `P2` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

In `ActionExecutor._reject_parts` the lease loop skips only parts whose type is neither `PART_TYPE_IMAGE_REF` nor `PART_TYPE_AUDIO_REF`: an `audio_ref` must resolve in the store with `ref.run_id == call.run_id`, be unexpired on the executor clock and carry `size == ref.size`, else `ERROR_INVALID_RESULT` with the same message forms (the provider's result is not adopted). `_discard_images` discards both reference types (name kept; docstring "every attachment reference"). The observation-size rule already goes through `observation_size` (P1). No other executor logic changes in this step (R10 is P3).

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC14
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

[`core/actions.py`, `tests/test_observations.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_observations.py`, through `runtime_context(attachments=AttachmentStore(clock))` and a provider returning one `audio_ref`: leased to another run → `error invalid_result`, provider result not fed (`result is None`, parts `()`); expired after `clock.advance` past the TTL → `invalid_result`; `size` off by one byte → `invalid_result`; a valid one is adopted with the part intact; after `store.release(run_id)` `lookup` returns `None`; a rejected observation discards the run's own audio while another run's stays. AC14's last clause (`attachment_expired` at the model turn) is proved in P7.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (invariant: no partially adopted observation). The size check compares against the stored size, not the header's; the discard stays idempotent with `release(run_id)`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase2): ...`, `fix(phase2): ...`, `test(phase2): ...`, `refactor(phase2): ...`).
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
