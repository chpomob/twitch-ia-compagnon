# Step P2 — Loader

Plan step `P2` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: branching resolution, and a refusal path before any effect.
  - **Manifest.** `_validate_manifest` accepts the optional `input_families` key through `parse_input_families`. `ManifestDeclaration` gains `input_families: tuple[InputFamilyDeclaration, ...] = ()` and `declares_input_families: bool`, where "absent" is distinct from `[]`, as R3's undeclared-publisher rule needs. A v1 manifest that carries the key is refused (D3).
  - **Pure resolver.** A new pure function, `resolve_input_ownership(enabled: Sequence[str], declarations: Mapping[str, ManifestDeclaration], inputs: Mapping | None) -> InputOwnership`, implements D2. `InputOwnership` is frozen and exposes:
    - `owner(family, platform) -> str | None`. `None` means unowned, both for an explicit `none` and for a pair nobody owns.
    - `origin(family, platform)`: `"explicit"`, `"default"` or `"unowned"`.
    - `candidates(family, platform) -> tuple[str, ...]`: every enabled declarer.
    - `rows()`: the resolved domain, sorted.
    - `view_for(module) -> OwnedInputs`.
    - `excluded(family) -> frozenset[str]`: the concrete platforms whose owner differs from the `(family,'*')` owner.
    - `is_set_none(family, platform)`.
    - `constrains_any() -> bool`: true when at least one resolved pair has an owner or is explicitly `none` (D3, used by P4's v1 route).
  - **`OwnedInputs`.** Defined in `core/loader.py` and re-exported for runtime use; P4 moves it into `core/runtime.py` if import order requires. It is a read-only frozen view:
    - `pairs: frozenset[tuple[str, str]]`;
    - `owns(family, platform)`, which answers `(family, '*')` ownership minus `excluded(family)`;
    - `platforms(family)`;
    - the constructor `OwnedInputs.unrestricted()`, the view used when no ownership table is installed (P4, P8).
  - **Refusals.** All are collected first, then raised as one `ModuleLoadError` whose `diagnostics` lists one value-free line per refusal. Each line names `family`, `platform` and the module names or value; none names a configured secret.
    - (a) Two or more same-specificity `default: true` declarers and no explicit entry. The line is `inputs: family 'chat': platform 'x': owned by default by both 'a' and 'b'; set inputs.chat.x`.
    - (b) An owner value that is a list of two or more modules. The line names both modules.
    - (c) An owner that is not enabled.
    - (d) An owner that does not declare that family for that platform. For the `'*'` key, the owner must declare `'*'`.
    - (e) A family outside the vocabulary.
    - (f) A malformed `inputs` entry: a non-mapping, or a non-text platform key.
  - **Order in `activate_enabled`.** The resolver is called in `activate_enabled` immediately after `_validate_compatibility` and **before** secrets resolution, settings validation, declaration registration and any `prepare`/`activate`. The result is stored on `self.input_ownership`.
  - **Ownership-aware settings hook.** `_validate_settings` passes `owned_inputs=self.input_ownership.view_for(name)` to a hook only when the hook declares that keyword (D7).
  - The resolver contains no platform or vendor literal (R2, AC11).

## Requirements

Plan mapping: R2
Acceptance criteria owned by this step: AC6, AC7, AC8, AC9
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

[`core/loader.py`, `tests/test_input_ownership.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_input_ownership.py`, using tmp_path test modules (`make_module`-style manifests that declare `input_families`):
  - **AC7.** Two modules both declaring (`chat`, `x`) `default: true` give exactly 1 diagnostic naming `chat`, `x` and both names. A spy `activate`/`prepare` records 0 calls, and a socket-creation spy (the phase-4 pattern in `tests/test_config_ui.py`) records 0.
  - **AC8**, with test modules standing in for `twitch` and the bridge (the shipped ones come in P8/P12):
    - an owner list of 2 is refused naming both;
    - an owner that is not enabled is refused;
    - an owner that does not declare the platform is refused;
    - `weather` is refused, naming the family, the platform and the value.
  - **Explicit and default precedence.** Explicit beats default; the `'*'` key applies to undeclared platforms; a concrete declarer beats a wildcard declarer; a wildcard-only declarer owns `x` by fallback.
  - **`none`** leaves the pair unowned (AC9 last clause).
  - `view_for` returns exactly the owned pairs, and `excluded` is correct for a `*` owner when one platform is assigned elsewhere.
  - `constrains_any()` is false with no declarer and no `inputs`, true with one `default: true` declarer, and true with only `inputs: {chat: {x: none}}`.
  - A hook with `owned_inputs` receives the view; a one-argument hook is still called with one argument.
  - A v1 manifest declaring the key is refused.

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

- Diagnostics must stay value-free. An `inputs` value could be a `${NAME}`-resolved string, so it goes through the same `_value_free_reason`/redaction path the loader already uses.
  - Resolution must run in the same order for `run` and `--check-config`. P3 wires `core/main.py:_validate_enabled` to the same resolver, and the parity test there guards it.

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
