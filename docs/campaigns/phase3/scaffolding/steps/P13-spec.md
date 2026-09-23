# Step P13 — Brain post-delivery `memory.record` step (R4; AC20, AC21, AC22-offer, AC23)

Plan step `P13` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **When the step runs.** After `_deliver_all` has produced the run's
    delivery outcome, and only if:
    - the session viewer is a platform viewer (not `system:`);
    - `memory.record` is bound for `(platform, channel, memory)`;
    - the run principal's authorized view grants it.
  - **The call.** One call is issued, with:
    - `viewer_text` = the triggering text[:200];
    - the branches are evaluated in this order, and the first match wins:
      1. **absent delivery** — the run produced no delivery entry at all
         (no route matched, the model answered nothing deliverable, or the
         run ended before delivery): `delivery: none` and no reply. This
         branch is explicit and precedes the others, because "every entry
         is `success`" is vacuously true of an empty list;
      2. the list is non-empty and every required entry is `success`:
         `reply_text` = the delivered text[:200] and `delivery: confirmed`;
      3. any entry is `external_unknown`: `delivery: unconfirmed` and no
         reply;
      4. otherwise (a failed delivery: `error`, `timeout`, `refused`):
         `delivery: none` and no reply.
  - **Budget.** The call uses the run's remaining deadline and one action
    call of the run budget.
  - **Skip.** Skipped, with a `memory_record: skipped` field in the run
    record/trace plus the reason, when 0 action calls remain or the
    remaining deadline is < 5 s (read from the spec's `timeout_seconds`, not
    a literal).
  - **No effect on the run.** The result never alters the terminal status,
    the delivery summary or the correlation fields.
  - **Offers.** `memory.record` is never offered: it is not proposable, so
    P6 excludes it. The step adds an assertion-level guard.

## Requirements

Plan mapping: R4
Acceptance criteria owned by this step: AC20, AC21, AC22, AC23
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

[modules/brain/__init__.py, tests/test_brain.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P6, P11]

All dependencies are already merged into `main` when this step runs.

## Tests

(with the real `viewer_memory` module on the fake platform and a
  scripted model)
  - AC20: `known: false`, then `known: true` with `interactions: 1` and a
    note `reply_text: hello`, `confirmed`.
  - AC21: `external_unknown` gives `unconfirmed` with no reply; `error`
    gives `none` with no reply; an **absent delivery** (a scripted model
    turn that produces no deliverable text, so the run has 0 delivery
    entries) gives a note with `at`, `viewer_text`, `delivery: none` and no
    `reply_text` key — never `confirmed`. Each run's status and delivery outcome
    equals a twin run without `viewer_memory` (compared field by field).
  - AC22 offer half: 0 `memory.record` occurrences in any model request
    body of a run where it is granted.
  - AC23: 0 calls remaining gives skipped; 4.9 s remaining (ManualClock)
    gives skipped; 1 call and 5 s gives exactly 1 call.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- The record call can itself time out at the run deadline. It must be
    awaited under the run's cancellation scope without converting a late
    failure into a run failure. Wrap it so any record outcome is traced
    only.
  - A delivery through a route (P5) must feed the same "delivered text".
    Use the text argument of the `chat.write`/`audio.speak` entry that
    succeeded.

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
