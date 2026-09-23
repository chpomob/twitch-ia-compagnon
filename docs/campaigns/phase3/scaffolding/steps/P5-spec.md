# Step P5 — Brain persona and ordered routes (R1; AC1, AC2-validator, AC3, AC4)

Plan step `P5` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Manifest.** Declare `persona` (string, maxLength 2000) and `routes`
    (array, maxItems 16), with each route holding:
    - `name` (required, unique);
    - `match.kinds` (required, non-empty, drawn from `EVENT_KINDS`);
    - `match.command` (optional, one token without whitespace, at most 32
      characters);
    - `match.audience` (optional, one of `broadcaster`, `moderators`,
      `vips`, `subscribers`, `everyone`);
    - `instructions` (optional, at most 2000 characters);
    - `delivery` (optional, the existing delivery-list shape).
  - **`validate_settings` names the field for:** a duplicate name, an
    oversized persona, instructions or command, a 17th route, and an unknown
    kind.
  - **`_system_prompt(route)`.**
    - With neither key set it returns the phase 2 constant **unchanged**
      (the same object/text).
    - A `persona` replaces only the opening identity line.
    - The fixed tool-usage and plain-text lines are always kept.
    - The matched route's `instructions` are appended as their own paragraph.
  - **Route match.** Evaluated once per run from the triggering event:
    - The kind must be in `kinds`.
    - `command`, when set, must equal the first whitespace-separated token
      of the text, compared case-insensitively as a whole token (`!brb` ≠
      `!brbx`; `!ask !brb` does not match).
    - `audience`, when set, is satisfied only by trusted role claims with
      provenance, reusing the trigger engine's audience semantics (not the
      text).
    - The first match wins. With no match the phase 2 path is unchanged.
  - **Route delivery.**
    - `delivery` replaces `_delivery_for(platform, channel)` for that run.
    - Route lists are resolved at `prepare` through the existing
      `_resolve_delivery`, with the same diagnostics.
    - **Startup safety check:** a route whose list holds any write other than
      `chat.write` and `audio.speak` must declare `audience` `broadcaster` or
      `moderators`, or have `kinds ⊆ PLATFORM_NOTICE_KINDS`. Otherwise
      `prepare` fails naming the route.
  - **Allowlisted test.** Rewrite
    `test_manifest_declares_v2_shape_settings_hook_and_no_grant` so its
    settings set is the phase 2 set plus `persona` and `routes`.

## Requirements

Plan mapping: R1
Acceptance criteria owned by this step: AC1, AC2, AC3, AC4
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

[modules/brain/__init__.py, modules/brain/module.yaml, tests/test_brain.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2, P4]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC1: without `persona`/`routes`, the system message of a run is
    byte-equal to a phase 2 golden captured from `_system_prompt()` before
    the change (stored as a literal in the test), on twitch and on the fake
    platform.
  - AC2: a 30-character persona is line 1, and both fixed lines are present.
    `validate_settings` rejects a persona of 2001 characters, 17 routes, a
    duplicate `name` and a 33-character `command`, each naming the field.
    The `--check-config` exit 2 of the same cases runs in P22.
  - AC3: routes A/B from the spec on the fake platform:
    - a raid notice delivers through exactly 2 entries (1 `chat.write`, 1
      `audio.play` through a scripted action binding);
    - `!missed recap` has X in the system message and 1 `chat.write` from
      the destination's list;
    - a plain mention matches no route and X is absent.
  - AC4:
    - an unsafe `!brb` route fails `prepare` naming it;
    - with `audience: broadcaster`, a trusted broadcaster `!brb` gives 1
      scene call (scripted `stream.scene.set` binding);
    - a text-claimed broadcaster gives 0;
    - `!ask !brb` matches no route.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- Byte-identity (AC1) breaks if the prompt is rebuilt by joining parts.
    Mitigation: with no persona or route, return the untouched constant.
  - Route delivery lists interact with phase 2 delivery accounting
    (`_invoked_count`, `_confirmed_sends`). Mitigation: reuse the same
    `_DeliveryEntry` tuples, so the accounting code is unchanged.

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
