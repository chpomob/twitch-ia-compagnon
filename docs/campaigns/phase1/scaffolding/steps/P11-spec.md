# Step P11 — Delivery list: resolution at `prepare`, terminal step over the list, `deliveries[]`, memory rule (R1 decision 1; AC49–AC54, AC56, AC57, AC58)

Plan step `P11` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Remove `DELIVERY_ACTION`; add `_DeliveryEntry(action, version, scope, text_argument: str | None, arguments: Mapping)` and `_resolve_delivery(config, catalog) -> tuple[_DeliveryEntry, ...]` run in `prepare` before the probe and before `mark_ready` for the default list and every override: `fixed` entries are checked against `self._actions.discovered()` (`unknown_action`), the spec's nature/`delivery` (`not_a_delivery`), the text mapping (`text_mapping_missing` when the entry names an argument absent from the schema or names one while the declaration says `none`; `text_mapping_ambiguous` when the entry's `text_argument` differs from the declaration's or a constant argument is given under the text argument's name; absent `text_argument` inherits the declaration's), and `empty` for `[]`; `modules` mode takes every discovered spec with `delivery`, ordered by `preference[]` then catalog order, ignoring unknown preference names (listed as `ignored` in the trace), `empty` when none; a failure raises `BrainModuleError` with the diagnostic `module 'brain': delivery: <entry path>: <reason>` naming `delivery.actions[<i>]`, `delivery.overrides["<k>"].actions[<i>]`, the derived action name, or `delivery`; the resolved lists are published as one `brain.delivery.resolved` trace (`default`, `overrides`, `ignored`). `run` selects the run's list (override by `platform/channel_id`, else default) and `_deliver_all(run, text, entries, *, next_call_index)` invokes each entry in order whatever the previous outcomes: destination `(run platform, run channel_id, entry.scope)`, principal `brain`, `call_id = <run_id>/call-<n>` continuing the run's counter, deadline `min(now + action_seconds, total_deadline)`, arguments = constants + `{text_argument: text}` when declared; per-entry outcome recorded as `deliveries[]{action, call_id, text, status}`; `delivery` summary per R1 (single entry → its status; all `success` → `success`; else first non-success in list order). Memory is written only when every text-receiving entry ended `success`; `external_unknown` never memorises. `RunOutcome.correlation` carries `deliveries`, `delivery`, `action_calls`. The run body in this step still performs one model turn (P12 replaces it), so today's tests keep passing with `call-1` as the sole delivery.

## Requirements

Plan mapping: R1
Acceptance criteria owned by this step: AC49, AC54, AC56, AC57, AC58
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

[`modules/brain/__init__.py`, `tests/test_delivery.py`, `tests/test_brain.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P8, P10]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_delivery.py` (fixtures enabled through `runtime_context` + the loader on `tests/fixtures/modules`): AC49 (single entry identical to today: `deliveries == [{chat.write, call-1, true, success}]`, inherited mapping); AC50 (two-entry text + effect, exact arguments, both orders); AC51 (two text entries, tools never list `chat.write`/`audio.say`); AC52 (`modules` mode: resolved order, `overlay.raw` absent, `ignored` preference, list `[chat.write]` when fakeeffects is disabled); AC53 (each of the 7 fixed lists and `modules` with no delivery-capable action fails `prepare` with the named entry and reason, 0 scenario requests, 0 sends, not ready); AC54 (per-entry outcomes, `FAIL_AFTER_EMISSION` → `external_unknown`, raising scene provider → `error`, refused scene entry with the chat send still made); AC57 (override on `fake/chan-b`, diagnostic path for an override naming `nope.action`); AC56 (grep of `modules/brain/` for the 5 literals is 0 lines; scenarios use the shipped brain with only `delivery` changed). `tests/test_brain.py`: `send["call_id"] == f"{run_id}/call-1"` unchanged.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (branching on catalog state at prepare; invariant: the model never sees a delivery action). The catalog at `prepare` must already contain every enabled module's declarations — the loader registers declarations before any `prepare` (P0 order), which the AC52 test on enabling/disabling `fakeeffects` verifies. Override keys are compared against `SessionKey.platform/channel_id`, never against a Twitch-specific id.

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
