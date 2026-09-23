# Step P1 — Event-kind vocabulary, `kind` validation and `model_proposable` in `core/contracts.py` (R1, R5, R6; AC25-core, AC39-core)

Plan step `P1` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Vocabulary.** Add
    `EVENT_KINDS = ("message", "sub", "resub", "sub_gift", "community_sub_gift", "raid", "follow", "tip", "watch_tick")`
    as an ordered tuple, plus a frozenset view.
  - **Named subsets.** Add `EVENT_KIND_DEFAULT = "message"` and
    `PLATFORM_NOTICE_KINDS`, which is every kind except `message` and
    `watch_tick` (R1 route safety).
  - **Validation.** Add `validate_event_kind(value, field)`: `None` → the
    default; a non-member or a non-string raises
    `ContractError(field, ...)`.
  - **Action spec flag.** Add `model_proposable: bool = False` to
    `ActionSpec`. `__post_init__` refuses a non-bool value. It also refuses
    `True` with `nature == "read"` (message "a read action is never
    model-proposable") and `True` with a delivery capability (message "a
    delivery-capable action is never model-proposable").
  - **Scope.** No platform, vendor or module name appears in either list.

## Requirements

Plan mapping: R1, R5, R6
Acceptance criteria owned by this step: AC25, AC39
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

[core/contracts.py, tests/test_contracts.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[] (none — can run first)

All dependencies are already merged into `main` when this step runs.

## Tests

- The vocabulary equals the nine names, and `PLATFORM_NOTICE_KINDS` has 7.
  - `validate_event_kind` accepts each kind and `None` (→ `message`), and
    refuses `"Raid"`, `""`, `1` and `"announcement"`.
  - `ActionSpec(... model_proposable=True, nature="read")` raises, and so
    does `True` with a `delivery` block (AC25 core half).
  - A write without delivery accepts `True`; `model_proposable="yes"` raises.
  - The default is `False` on every existing construction.
  - A word scan of `core/contracts.py` finds 0 occurrences of `kick`,
    `youtube` and `twitch` (AC39 core half; the full scan runs in P26).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

`ActionSpec` is frozen and may be serialized or compared
  elsewhere (proxy lending, capability view). Mitigation: add the field last,
  with a default. The step greps every `ActionSpec(` construction and every
  `dataclasses.asdict`/field iteration over it (see the caller table), so the
  proxy's spec equality still holds for specs with the default.

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
