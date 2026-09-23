# Step P19 — Kick `chat.write`, rate limits, `timeout`-only moderation service and capability view (R7, R2/R5 capability halves; AC35-rest, AC12-kick, AC27-kick, AC39-kick)

Plan step `P19` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **`chat.write`.** The manifest declares `chat.write` for `kick/*/chat`
    with delivery text mapping as twitch does. A send is one POST to the
    platform chat endpoint:
    - more than 500 characters → `error text_too_long`, 0 requests;
    - 429 → `error rate_limited`, and further sends are blocked until the
      announced retry instant (`refused rate_limited`, 0 requests);
    - lost/5xx → `external_unknown`, never retried.
  - **Moderation service.** Published only when services are available,
    with `operations = {timeout}`. Durations are rounded **up** to whole
    minutes, and a result outside 1–10080 minutes is refused locally with 0
    requests. The request is one POST to the bans endpoint with a
    `duration`. It never omits the duration.
  - **No clip or poll service.**
  - **Catalog assertion (decision 11).** Declaring `chat.write` in the kick
    manifest makes it a second declarer. In `tests/test_examples.py`,
    `test_manifests_are_unique_and_have_coherent_capabilities` is rewritten
    in this step, and only in its `chat.write` multiplicity assertion, to
    the running value: `chat.write` is declared by exactly twitch and kick.
    P21 moves it to twitch, kick and youtube; P23 pins that final value.
    The manifest count (set by P18) and the action-name count (unchanged:
    `chat.write` is not a new name) are not touched.

## Requirements

Plan mapping: R7, R2, R5
Acceptance criteria owned by this step: AC35, AC12, AC27, AC39
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

[modules/kick/__init__.py, modules/kick/module.yaml, tests/test_kick.py, tests/test_clips.py, tests/test_moderation.py, tests/test_examples.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P9, P14, P18]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC35 rest: 500 characters give 1 request; 501 give `text_too_long` and
    0; a 429 with retry 30 s gives `rate_limited`, and a send 29 s later is
    `refused rate_limited` with 0 requests; 90 s is sent as 2 minutes;
    10081 minutes gives 0 requests.
  - Catalog: the rewritten `chat.write` multiplicity assertion passes with
    exactly {twitch, kick}, and the full suite is green at the end of the
    step (decision 11).
  - AC12 kick half (in `tests/test_clips.py`): with twitch + kick,
    `stream.clip.create` is ready for twitch and unbound for kick with
    `platform_unsupported`. With only kick: `required: true` fails naming
    `clips`/`platform_unsupported`; unset starts with the action unbound.
  - AC27 kick half (in `tests/test_moderation.py`): `delete_message` on
    kick in `act` gives `platform_unsupported` and 0 requests.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

the Kick rate-limit header shape (a `Retry-After` in seconds or
  a reset timestamp) must be read defensively. An unparsable answer blocks
  for a bounded default and is traced.

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
