# Step P12 — Bridge manifest, settings validator, package data and count adaptations (R1, R4, R6, R10; AC5 schema, AC15, AC22, AC23 titles, AC41; allowlisted test_examples/test_manifest_presentation/test_profiles)

Plan step `P12` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: a public manifest, a new action contract, and a packaging change.
  - **`module.yaml`, manifest v2:**
    - `runtime_api: 2`, `lifecycle.roles: [input]`.
    - `produces: [channel.chat.message]`.
    - `credentials: [password]`.
    - `settings_validator: validate_settings`.
  - **`settings_schema`.** Every node has a `title` and machine-readable `default` where one exists. There is **no `required`** anywhere (AC5).
    - `url`: string.
    - `password`: string.
    - `deadlines`: `connect_seconds`, `authentication_seconds`, `request_seconds`, `acknowledgement_seconds`.
    - `reconnect`: `min_delay_seconds`, `max_delay_seconds`.
    - `queue_max_events`.
    - `allowlist`: an array of `{action}`|`{group}` entries with an optional `arguments` schema.
    - `companion_identities`: platform → id.
    - `instance_identity`: platform → broadcaster account id, used to default `channel_id` and to scope `users.read`.
    - `trust_instance_roles`: bool, default false.

    The validator enforces that `url` is present even though the schema has no `required` (AC22).
  - **`input_families`:**
    - `chat` on `[twitch, kick, youtube]`, default false;
    - `viewers` on `[twitch, kick, youtube]`, default false;
    - `cues` on `['*']`, default true.
  - **`triggers` declaration.** The trigger types the native chat inputs declare, plus `event_kind`. The default policy is `any_of`: `event_kind: [cue]` OR `keyword: ['${companion_name}']`. These are existing rule types (`core/triggers.py:128-131`, `COMBINATION_OPERATORS`).

    **Amendment (2026-10-15, second gap of the P12 blocker report).** A default policy that names
    `${companion_name}` requires the module to configure one: `core/loader.py` reads
    `companion_name` from the module's own settings, `core/main.py` builds the trigger registry
    without a fallback name, and `core/triggers.py:1342` raises "uses ${companion_name} but no
    companion name is configured". As the step was first written, **any profile enabling the bridge
    would fail startup at trigger registration**, contradicting the R1 standing decision that the
    bridge is optional and that its absence or misconfiguration never breaks the companion.
    Therefore the `settings_schema` also declares an optional `companion_name` string setting (with a
    `title`, no `default`, no `required` — AC5 holds), and `validate_settings` refuses a missing or
    blank one with a value-free diagnostic naming the field, exactly as it does for `url`, so
    `--check-config` reports it without opening a socket.
  - **`actions`:**
    - `automation.action.run`: `nature: write`, `idempotency: none`, `required_permissions: [automation.run]`, `destinations: [{platform: '*', channel_id: '*', scope: automation}]`, `delivery: {text_argument: none}`, not model-proposable.
    - Its `argument_schema` is `{type: object, properties: {action: {type: string, minLength: 1, maxLength: 128}, arguments: {type: object}}, required: [action], additionalProperties: false}`.
      **Amendment (2026-10-15, first gap of the P12 blocker report).** AC15 requires *this schema
      itself* to refuse an `action` of 0 or 129 characters, and the kernel's schema subset did not
      support `minLength`/`maxLength` — declaring the action would have failed at load with
      `SchemaError: ... unsupported schema keyword`. Step **P12F1** adds both keywords to
      `core/contracts.py` (deviation D9). They must be present in this manifest exactly as written;
      do not move the length check into the provider and do not touch AC15's wording.
    - `timeout_seconds` is a fixed value; the validator refuses an `acknowledgement_seconds` greater than it minus a margin (R4: timeout ≥ acknowledgement deadline).
    - The description contains the sentence "Acceptance by the automation application is not a confirmation that the action ran."
    - `users.read` is **not** copied into the manifest. It is declared at prepare from the users manifest (P18), and the manifest comment says so, so the spec stays single-sourced. If the loader requires every registered action to be manifest-declared, the declaration is instead copied verbatim and a test asserts equality with the users manifest (decide at build by running `tests/test_loader.py`).
  - **`__init__.py` (this step only):**
    - `validate_settings(settings, *, owned_inputs=None) -> list[str]` (D7). It produces value-free diagnostics `module 'streamerbot': field 'X': reason` for: a missing `url`; a scheme not `ws`/`wss`; a non-loopback host without `wss` (reusing the loopback rule of `modules/agent_link/__init__.py`, read and mirrored, not imported if it is private); deadlines and delays that are non-positive or non-finite; `min_delay > max_delay`; a non-positive integer `queue_max_events`; duplicate `action:` names or `group:` names (AC45 first half); an entry with both or neither key; an invalid `arguments` schema (via `validate_schema`); and, when `owned_inputs` owns (`chat`, P), a missing `companion_identities.P`, naming the platform (AC33 first half).
    - An `activate` returning a handle whose `prepare` registers the run action with a provider that answers `refused provider_not_ready`, plus `close`. P14 replaces it.
  - **No defaults.** There is no default endpoint, port, password, version or action name anywhere (R6).
  - **`pyproject.toml`:** add `"modules.streamerbot" = ["module.yaml"]`. Dependencies are unchanged (memory "phase1-module-step-pyproject-line").
  - **Allowlisted count adaptations, 17 → 18:**
    - `tests/test_examples.py`: `MODULE_NAMES`, `EXPECTED_MANIFESTS`, `DECLARATION_KEYS` with `input_families`, `DECLARED_ACTIONS` with `automation.action.run`. **Amendment (same report):** the same allowlisted test carries two more assertions the step must adapt — `SECRET_SETTINGS` needs the bridge's `("password",)` entry, and the `len(declared) == 13` / `declared == PROVIDED_ACTIONS["pc"] | {...}` assertions need `automation.action.run`. The blocker report located all three at
      `tests/test_examples.py`; they belong to the same test function, so the allowlist entry does not grow.
    - `tests/test_manifest_presentation.py`: the count.
    - `tests/test_profiles.py`: both count tests.

## Requirements

Plan mapping: R1, R4, R6, R10
Acceptance criteria owned by this step: AC5, AC15, AC22, AC23, AC41
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-5 decisions that override any contrary reading of a requirement:
- **The bridge is optional in every case.** Absent, disabled or unreachable, the companion behaves
  exactly as it does today: no setting becomes required at startup, no default path changes, and no
  shipped profile depends on it. An unreachable instance produces a *ready* companion with an
  explicitly unavailable capability — never a failed startup.
- **The minimum scope is the sink and the triggers, together**: the sink runs a named action of the
  user's instance behind our own authorization, deadlines and honest terminal outcomes; the triggers
  let an event the instance routes enter the companion through the ordinary trigger policies.
- **Its events may REPLACE our basic inputs (chat, viewers) — never in parallel.** For every
  (family, platform) pair there is at most one active provider, resolved at preparation from the
  profile and the manifests, and an ambiguous configuration is refused with a diagnostic naming the
  family and both providers. Counting on the deduplication window to hide two providers is excluded.
- **Do not hide the integration's defects.** An execution the instance cannot confirm is never
  reported as a success: an acknowledged run ends `external_unknown` with its cause, and the model
  and the operator both see it.
- **This phase is NOT "delivered" at merge.** The README and the documentation index carry
  "not delivered — awaiting real-instance trial" until the trial record exists, and a test enforces it.
- **The vendor and product names never appear in `core/`.** The kernel vocabulary stays vendor-free:
  action `automation.action.run`, permission `automation.run`, destination scope `automation`,
  families `chat`/`viewers`/`cues`.
- **The approved plan's deviations D1–D8 are decisions of this phase** and override any contrary
  reading of the specification — including the two files beyond the specification's cost row:
  `core/actions.py` (`excluded_platforms`, D5) and `core/triggers.py` (`add_companion_identities`,
  D6). The D8 transmission-boundary canary must stay green; if an aiohttp upgrade breaks it, take the
  fallback route D8 documents and say which route you took in the commit message.
- The configuration UI stays a **local-only** surface; the ownership table and its diagnostics are
  displayed **read-only**, and nothing in the UI can widen the authorization set or write the
  `secrets` and `actions` blocks.

## Files

[`modules/streamerbot/module.yaml`, `modules/streamerbot/__init__.py`, `pyproject.toml`, `tests/test_automation_bridge.py`, `tests/test_examples.py`, `tests/test_manifest_presentation.py`, `tests/test_profiles.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2, P6, P10, P12F1]

P12F1 (the kernel's string length bounds, deviation D9) must have landed first: without it, declaring
this manifest's `argument_schema` fails at load with `unsupported schema keyword`.

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_automation_bridge.py` (new):
  - **AC15.** The discovered spec fields, exactly. The schema accepts `{action:"a"}` and refuses a missing `action`, an extra key, and lengths 0 and 129. The description contains the test-defined constant sentence.
  - **AC22.** `ws://192.0.2.1:9/` is refused; `ws://127.0.0.1:9/` and `wss://192.0.2.1:9/` are accepted. A missing `url`, a `0` deadline and a `nan` deadline are each refused with one diagnostic naming the field and carrying no value.
  - **AC23 (titles).** `tests/test_manifest_presentation.py` passes for 18, and `password` is in `credentials`.
  - **AC5 (schema).** The schema has no `required` key at any depth.
  - **AC45 (validator half).** Duplicate entries are refused.
  - **AC33 (validator half).** `owned_inputs` owning (`chat`, twitch) without `companion_identities.twitch` gives one diagnostic naming `twitch`.
  - **AC41.** The dependencies are exactly `aiohttp` and `PyYAML`, and the package-data line is present. The installed-distribution test discovers 18.
  - `grep -ri` of `core/` for the module and vendor names returns 0 (AC11, re-run).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 3,124 passed / 18 skipped, the count at the start of
this phase; the seven allowlisted tests are adapted as the specification lists them, never weakened.

## Risks

- The phase-4 `default` annotation must not create a hidden default URL: `url` has no `default`.
  - The manifest's YAML comments must not use the vendor name inside `core/` (they live in `modules/`, which is allowed).
  - AC5's "no required" depends on the validator, not the schema, refusing a missing `url`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase5): ...`, `fix(phase5): ...`, `test(phase5): ...`, `refactor(phase5): ...`, `docs(phase5): ...`).
  The scope MUST be `phase5`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase5)`.
- Keep the phase-0 to phase-4 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, the phase-3 moderation/memory/watch semantics, and the phase-4 configuration UI's
  local-only, read-only-for-secrets-and-actions surface.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
