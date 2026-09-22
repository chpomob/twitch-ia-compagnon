# Step P12 — Audio attachments over protocol v1

Plan step `P12` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Proxy: `_on_attachment_header` accepts `content_type ∈ ATTACHMENT_CONTENT_TYPES` (from `core.contracts`; the `invalid_frame` message says "an accepted content_type"); the binary bound `min(store max_object_bytes, max_attachment_bytes)`, the three-step transfer, acknowledgement, call identity, uniqueness and no-retransmission rules are untouched; `_resolve_part` and the §5.5 check treat `PART_TYPE_AUDIO_REF` exactly as `PART_TYPE_IMAGE_REF` (an unacknowledged `attachment_id` → `error attachment_refused`, `retryable: false`, uploads discarded, or `external_unknown` for a write not reported `refused`), and the translated part keeps every other field, `transcription` included, verbatim. Agent link: `_observation_with_transfers`, `_transfer` and `_discard` select parts of either reference type; a refused transfer returns `error attachment_refused` and never a dangling reference. `docs/proxy-protocol.md`: a "Phase 2 addendum" section stating `attachment.content_type ∈ {image/png, image/jpeg, audio/wav}`, that an `observation` may carry `audio_ref` parts under the §5.5/§10 acknowledgement rule, that `v` stays 1, and the remote-readiness limit (decision 11 of the spec); the frame table, limits and codes are otherwise unchanged.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC18, AC19, AC20
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

[`modules/proxy/__init__.py`, `modules/agent_link/__init__.py`, `docs/proxy-protocol.md`, `tests/test_proxy.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1, P5]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_proxy.py`: AC18 — over the in-memory pair, an agent-side provider storing a 96 044-byte segment sends 1 `attachment` header (`content_type: "audio/wav"`, `size: 96 044`), 1 binary frame of 96 044 bytes, receives `attachment_ack accepted: true`; the brain's observation carries the `audio_ref` translated to a brain-store attachment of 96 044 bytes leased to the run with its `transcription` verbatim; `content_type: "audio/mpeg"` → `error invalid_frame`, nothing stored; a 960 044-byte segment passes under the default bound; `size: 1 048 577` → `attachment_ack accepted: false`, code `attachment_too_large`, on the header alone. AC19 — an `observation` naming an unacknowledged `attachment_id` in an `audio_ref` → `error attachment_refused` (`retryable: false`), uploads discarded, 0 attachments leased; the agent facing a refused transfer returns `error attachment_refused`; a forwarded `audio.speak` (declared in the harness catalog) whose connection drops after the `call` frame → `external_unknown` cause `proxy_disconnected`, not retransmitted on reconnect, its late `observation` ignored and counted. AC20 — every frame in the audio tests has `v == 1`; a read of `docs/proxy-protocol.md` finds the addendum and the unchanged code list; `tests/test_proxy_process.py` (unchanged) still passes.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (network boundary; invariant: no reference without an acknowledged upload). The proxy must not read the part's `content_type` to pick the store's type — the acknowledged upload's header decides; the agent's discard on refusal covers both reference types.

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
