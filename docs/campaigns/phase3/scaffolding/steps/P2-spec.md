# Step P2 — Built-in trigger type `event_kind` in `core/triggers.py` (R1, R6; AC3/AC5 policy prerequisites, AC29 default policy)

Plan step `P2` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Declaration.** Add `TRIGGER_TYPE_EVENT_KIND = "event_kind"` to
    `BUILTIN_TRIGGER_TYPES`. Its canonical schema is `{kinds: array, items:
    enum EVENT_KINDS, minItems: 1, uniqueItems: true}` with
    `additionalProperties: false`.
  - **Normalization.** `_normalize` reads `payload.kind` through
    `validate_event_kind`. An invalid value is refused
    `invalid_payload:payload.kind` with the identifiers already established.
    The kind joins `_Normalized`.
  - **Evaluation.** Add `_evaluate_event_kind(parameters, kind)`, a pure
    membership test that is never handed the text or the RNG, so it makes
    0 draws. Wire it into the explicit `_evaluate_rule` dispatch and into
    `_validate_builtin_rule`.
  - **Keyword rule.** An event whose text is empty or absent never satisfies
    a keyword rule, even for a mention-of-companion default. Verify the
    current `_evaluate_keyword` behaviour on `""`, and make it explicit if it
    is not already so.
  - **Composition.** The existing combination operators (`all_of`, `any_of`,
    `none_of`) compose the new type unchanged.

## Requirements

Plan mapping: R1, R6
Acceptance criteria owned by this step: AC3, AC5, AC29
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

[core/triggers.py, tests/test_triggers.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1]

All dependencies are already merged into `main` when this step runs.

## Tests

(new cases in `tests/test_triggers.py`, all with a counting RNG
  double)
  - `event_kind [raid]` accepts a raid event and rejects a message and an
    event without `kind`, with 0 draws each time.
  - `any_of: [keyword !ask, event_kind [raid, sub]]` accepts `!ask x` and a
    `sub` notice with empty text, and rejects a plain message.
  - `all_of: [audience moderators, event_kind [message]]` accepts a trusted
    moderator message and rejects the same text from an untrusted author.
  - Refusals: a policy naming `kinds: [announcement]`, `kinds: []` and
    `kinds: [raid, raid]` is refused at registration naming the field.
  - A keyword rule on a `follow` notice with empty text rejects.
  - An event with `payload.kind: "bogus"` is refused as invalid payload.
  - The existing tests that iterate `BUILTIN_TRIGGER_TYPES` filter by name
    and stay unchanged.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

a module that declares `event_kind` in its manifest before this
  step lands would fail discovery, so manifests only declare it from P4 on.
  The dispatch must not read the kind for the other three types, or
  determinism tests pinned on draw counts could shift.

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
