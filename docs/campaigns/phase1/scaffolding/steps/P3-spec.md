# Step P3 — Executor validates observation parts, observation size and `image_ref` leases; not-ready provider is `refused` (R4; AC22, AC47, AC23-shape, `provider_not_ready`)

Plan step `P3` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`ActionExecutor.__init__` gains `attachments: AttachmentStore | None = None` and `max_observation_bytes: int | None = None` (validated positive int; `None` = no size bound). In `_validate_observation`, for every terminal observation carrying parts: (a) `validate_parts` shape and `text` bound; (b) each `image_ref` must resolve in the store (`AttachmentStore.lookup(attachment_id)`) with `run_id == call.run_id`, unexpired at validation time on the executor clock, and `size` equal to the stored object size — otherwise the observation becomes `error` (`ERROR_INVALID_RESULT`), the provider's result is not adopted, and every `image_ref` the observation named is released through `AttachmentStore.discard(attachment_id)`; (c) `observation_size(parts) > max_observation_bytes` becomes `error` (`observation_too_large`) and releases every named `image_ref`. Synthetic executor observations carry no parts. In `invoke`, a binding whose module is not ready yields `refused` (not `error`) with code `ERROR_PROVIDER_NOT_READY`, provider uninvoked, so an action whose provider disconnected mid-run reads as a refusal to the model (R6/AC35). `core/main.py::_assemble_runtime` passes the `AttachmentStore` it already builds as `attachments` and leaves `max_observation_bytes=None`: the observation-size budget is the brain's (`budget.max_observation_bytes`, enforced in P12), the executor's bound is a runtime-wide guard a caller may set; one rule, two enforcement points documented in both. In `core/attachments.py` add the one public accessor this needs, `AttachmentStore.lookup(attachment_id) -> AttachmentRef | None` (run id, size, expiry; no TTL side effect) and `discard(attachment_id)` (drop one object, idempotent); nothing else in that file changes. `tests/conftest.py::runtime_context` gains `attachments: AttachmentStore | None = None` and passes it to both the context and the executor.

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC22, AC47, AC23
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

[`core/actions.py`, `core/attachments.py`, `core/main.py`, `tests/conftest.py`, `tests/test_actions.py`, `tests/test_observations.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_actions.py`: a provider returning a valid `image_ref` leased to the call's run is adopted with parts intact; an `image_ref` leased to another run, expired on the injected clock, or with `size` off by 1 byte becomes `invalid_result` and the object is released (store usage 0) (AC22, AC47 tail); an observation over `max_observation_bytes` becomes `observation_too_large` and releases its images (AC47 head); a not-ready module returns `refused` with `provider_not_ready` and 0 provider invocations; synthetic observations carry `parts == ()`. `tests/test_observations.py`: executor validation of `image_ref` through the shared `runtime_context(attachments=...)`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (branching, invariant: no partially adopted observation). The store's lease lookup must not extend TTL as a side effect; the release-on-refusal path must be idempotent with the run-end `release(run_id)`. Callers of `ActionExecutor(...)` are enumerated in the caller table below.

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
