# Step P11 — `viewer_memory` actions `memory.recall` and `memory.record` (R4; AC16-recall, AC22-module)

Plan step `P11` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Manifest.** Add:
    - `memory.recall`: read, permission `memory.recall`, destinations
      `*/*/memory`, args `{}` with `additionalProperties: false`;
    - `memory.record`: write, permission `memory.record`, destinations
      `*/*/memory`, no delivery, no `model_proposable`, timeout 5;
    - the `max_recall_bytes` setting (512–8192, 1024).
  - **`memory.record` arguments.** Unlike `memory.recall` (empty schema),
    its schema accepts the exchange data (decision 6): `viewer_text`
    (≤ 200, required), `reply_text` (≤ 200, optional), `delivery` (enum
    `confirmed`, `unconfirmed`, `none`, required) and optional
    `display_name`, with `additionalProperties: false`. It forbids every
    viewer-identity argument: there is no viewer id, platform or channel
    property, so those are refused as invalid.
  - **Viewer.** The viewer comes from `conversation_id` (decision 6).
    `system:`-namespaced or unparsable → `error no_viewer`.
  - **Recall result.**
    - An unknown or expired viewer → `{known: false}`.
    - Otherwise `known`, `display_name`, `first_seen`, `last_seen`,
      `interactions`, then notes newest first, fitted to `max_recall_bytes`
      (measured as the serialized UTF-8 observation).
    - Oldest notes are dropped first. If the metadata alone is over the
      limit, `display_name` is shortened by whole characters from its end.
      `truncated: true` is set when anything was left out.
    - A recall that returns a record is a **use** (`use_count` + 1,
      `last_used`).
  - **Record.** Creates or updates the file (`interactions` + 1,
    `last_seen`, `display_name`, a use), with eviction and the note bounds
    from P10.

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC16, AC22
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

[modules/viewer_memory/__init__.py, modules/viewer_memory/module.yaml, tests/test_viewer_memory.py, tests/test_examples.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P10]

All dependencies are already merged into `main` when this step runs.

## Tests

(through the executor, so argument validation precedes the
  provider)
  - AC22 module half:
    - an argument `viewer_id` is refused as invalid with 0 provider
      invocations, on `memory.record` (alongside valid exchange data) and on
      `memory.recall` (whose schema is `{}`);
    - `memory.record` with only `viewer_text` and `delivery` succeeds, and
      with `reply_text` and `display_name` added also succeeds;
    - 12 notes of 200 characters at 1024 bytes give ≤ 1024 bytes, newest
      first, `truncated`;
    - 64 four-byte characters + 10 notes at 512 give ≤ 512 bytes, all
      metadata keys, and a `display_name` that is a prefix of the stored
      one;
    - an 80-character name is stored as 64;
    - 511 is rejected by the validator;
    - a `system:watch` conversation gives `error no_viewer`.
  - AC16 recall half: at 30 d + 1 s the result is `known: false`.
  - A recall increments `use_count`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- The observation budget measure must match the executor's
    `observation_size` or the brain's budget accounting would disagree.
    Mitigation: fit on the exact serialization the provider returns as its
    text part.
  - The `conversation_id` format is an assumption to verify first
    (`SessionKey.serialize()` and its inverse in `core/contracts.py`). If no
    inverse exists, parse with the same separator rule, without editing
    `core/`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase3): ...`, `fix(phase3): ...`, `test(phase3): ...`, `refactor(phase3): ...`).
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
