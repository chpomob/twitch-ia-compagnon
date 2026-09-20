# Step P15 — `modules/users`: directory of observed authors and `users.read` (R5; AC27, AC28, AC30)

Plan step `P15` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Manifest v2 (roles `[]`, `consumes: [channel.chat.message]`, `produces: []`, settings `max_channels`, `max_users_per_channel`, `max_age_seconds` (all required, finite, positive; validator), `actions: [users.read]`: read, permission `users.read`, destinations `*/*/chat`, args `{limit: integer 1..100 required, cursor: string optional}`, result `{users[]{user_id, display_name, roles?, roles_provenance?, first_seen, last_seen}, page{returned, has_more, next_cursor}, freshness{observed_at, window_seconds}, coverage{kind: "observed_authors", complete: false, retained_users, evicted}}`). `prepare` subscribes to `channel.chat.message` (synchronous handler: validates `payload.platform/channel_id/author.id`, updates the `(platform, channel_id)` directory keyed by `user_id`, `first_seen`/`last_seen` on the injected clock, `display_name` from `author.display_name`, `roles` only when `author.roles` is a list of strings and `author.roles_provenance` a non-empty string — decision 5), evicts oldest `last_seen` first beyond `max_users_per_channel` (counting `evicted` per channel), evicts least-recently-updated channels beyond `max_channels`, drops users older than `max_age_seconds` on read; registers the provider; marks ready. Pagination: users ordered by `last_seen` desc then `user_id`, opaque cursor = base64 of `platform/channel_id/last_seen/user_id`; a cursor from another channel or malformed → `error invalid_arguments`. `coverage.complete` is always `false`. One `text` part renders the page for the transcript.

## Requirements

Plan mapping: R5
Acceptance criteria owned by this step: AC27, AC28, AC30
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

[`modules/users/__init__.py`, `modules/users/module.yaml`, `tests/test_users.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P8, P9]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_users.py`: AC27 (150 authors, bound 100 → pages 40/40/20, `has_more` true/true/false, no repeat, `evicted == 50`, foreign cursor → `invalid_arguments`); AC28 (age eviction on the injected clock; `roles` present only with provenance, absent — not empty — otherwise, on the fake platform's events; `freshness.observed_at == clock()`); AC30 both platforms; bounds on channels; manifest test; default-deny without grant.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (bounded state, ordering guarantee under eviction). A stable cursor under concurrent eviction: the cursor encodes the sort key, so an evicted boundary user only shortens the page, never repeats one. Handler must never await (bus subscribers of the input path are synchronous in this runtime).

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
