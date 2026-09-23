# Step P26 — Full-branch review gate before PR delivery (R1–R8; AC39-scan, AC40, AC42, cross-file integration)

Plan step `P26` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Review the **entire branch diff** against the phase 3
base commit, reading all changed files together, not commit by commit.
  - **Why per-commit review is not enough.** Each step was reviewed alone.
    Cross-file contracts can be individually correct yet jointly broken:
    - the notice `kind` written by twitch/kick/youtube versus read by
      triggers, brain routes and the command consumers (decision 2);
    - the principal derived in the brain versus the rules in
      `presence.yaml.example`;
    - `conversation_id` produced by the brain versus parsed by
      `viewer_memory`;
    - the `moderation` service surface published by three platforms versus
      consumed by `moderation`;
    - the `clip` service surface versus `clips`;
    - `model_proposable` from loader to brain;
    - the capability view reasons across `clips` and `stream_control`.

    Component decomposition is not integration, so only a whole-branch read
    catches these.
  - **Checklist**, recorded in `docs/campaigns/phase3/gate-review.md`:
    1. `git diff --stat <base>..HEAD` touches only spec targets and
       `docs/campaigns/phase3/`.
    2. AC42: `git diff <base> -- config.yaml.example config.server.yaml.example agent.yaml.example`
       is empty, and so are `docs/design-v2.md`, `docs/proxy-protocol.md`
       and the root `spec.md`/`plan.md` of the MVP as the implementation
       sees them.
    3. AC39: a case-insensitive word grep of `core/` for kick, youtube,
       twitch and the module names is empty.
    4. AC40: pyproject has 17 lines, only `aiohttp` and `PyYAML`.
    5. The full suite passes in the project virtualenv with 0
       positive-duration sleeps. Record the pass/skip counts against phase
       2's 1891/12.
    6. Every requirement row of the coverage table below has a passing test.
    7. No permanent-ban request can be built (grep `permanent`, a ban
       without duration).
    8. No credential appears in traces (`trace_texts` scans exist for clips,
       moderation, kick and youtube).
    9. Every caller-table row was migrated.
    10. Every deviation flagged by a step (fixture ripples, spec gaps such
        as the P21 poll reason) is either resolved or escalated.
  - **Verdict.** APPROVE or REQUEST_CHANGES with file:line findings. Fixes
    loop back to the owning step id.

## Requirements

Plan mapping: R1, R8
Acceptance criteria owned by this step: AC39, AC40, AC42
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

[docs/campaigns/phase3/gate-review.md]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14, P15, P16, P17, P18, P19, P20, P21, P22, P23, P24, P25]

All dependencies are already merged into `main` when this step runs.

## Tests

the gate runs the whole suite (`pytest -q`) and the greps
  above. It writes no production code.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

the gate can pass on a green suite while a contract is only
  tested on the fake platform. Mitigation: item 6 requires each AC to cite
  a test on the real module named by the AC (twitch, kick, youtube).

## Caller tables

### `ActionSpec.model_proposable` (P1, P3, P6)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/contracts.py | `ActionSpec.__post_init__` | New field, default `False`; refusals on read/delivery. |
| core/loader.py | `_ACTION_KEYS`, `_manifest_actions` | Accept and pass the key (P3). |
| modules/brain/__init__.py | `_offered_tools`, model-call path | Offer proposable authorized writes; run limit (P6). |
| modules/proxy/__init__.py, modules/agent_link/__init__.py | spec equality/serialization for lent actions | No change expected: the default is equal on both sides. Verify in P1 that no field list is enumerated by hand; flag if one is. |
| modules/moderation/module.yaml | manifest | The only `true` (P14). |
| tests/test_contracts.py | spec construction | Unchanged plus new refusals. |

### Built-in trigger type `event_kind` and `payload.kind` (P2)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/triggers.py | `BUILTIN_TRIGGER_TYPES`, `_normalize`, `_evaluate_rule`, `_validate_builtin_rule` | Add the type and kind reading; the other three are unchanged. |
| modules/twitch/module.yaml, tests/fixtures/modules/fakeplatform/module.yaml | `triggers.types` | Declare `event_kind` (P7, P4). |
| modules/kick/module.yaml, modules/youtube/module.yaml, modules/watch/module.yaml | `triggers.types` | Declared at creation (P18, P20, P16). |
| modules/chat_context, modules/users | consume `channel.chat.message` | Unchanged; they see notices as chat events (decision 2). |
| tests/test_triggers.py | tests iterating `BUILTIN_TRIGGER_TYPES` | Filter by name; unaffected. |

### Brain run principal and routes (P5, P6, P13)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/brain/__init__.py | `_offered_tools`, `_delivery_call`, `_deliver_all`, `_delivery_for`, `_system_prompt`, `validate_settings` | Per-run principal and route; absent keys give phase 2 behaviour. |
| config.yaml.example, config.server.yaml.example | brain settings and grants | Unchanged (AC42); their rules name `brain`, which chat runs still use. |
| tests/test_brain.py | allowlisted manifest test | Settings set + `persona`, `routes`. |

### Runtime service kinds `clip` and `moderation` (P4, P8, P19, P21)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/twitch/__init__.py | activation | Publish `clip` and `moderation` (P8). |
| modules/kick/__init__.py, modules/youtube/__init__.py | activation | `moderation` only (P19, P21). |
| tests/fixtures/modules/fakeplatform/__init__.py | activation | The scripted services, as selected by `services` (P4). |
| modules/clips/__init__.py, modules/moderation/__init__.py | prepare | Resolve per platform. |
| tests/test_stream_control.py | registry `entries()` equalities | Its own registry fixture with poll only; unaffected (checked in P4). |

### Catalog 11 → 17 manifests (P9, P10, P14, P16, P18, P20, P23)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| tests/test_examples.py | the two allowlisted tests | Running values per step; final values in P23. |
| tests/test_profiles.py | the two allowlisted tests | Same. |
| tests/test_twitch.py | `test_manifest_declares_twitch_source_and_sink` | Rewritten in P7. |
| tests/test_main.py | `test_pyproject_ships_core_and_modules_with_every_manifest` | Unchanged; satisfied because each module step adds its line. |

Unknown callers: none are known outside the repository. Dynamic attribute
access on `ActionSpec` is the grep blind spot the spec names. The loader is
its only constructor from manifests.

## Shared-file rule (explicit ordering edges)

Each file that several steps touch is edited in this chain order, and every
pair is linked by a dependency path:

- `modules/brain/__init__.py`: P5 → P6 → P13.
- `modules/twitch/__init__.py`: P7 → P8.
- `modules/viewer_memory/__init__.py`: P10 → P11.
- `modules/moderation/__init__.py`: P14 → P15.
- `modules/watch/__init__.py`: P16 → P17.
- `modules/kick/__init__.py`: P18 → P19.
- `modules/youtube/__init__.py`: P20 → P21.
- `tests/conftest.py`: P4 → P8 → P18 → P20.
- `tests/test_contracts.py`: P1 → P3.
- `tests/test_triggers.py`: P2 → P4.
- `tests/test_clips.py`: P9 → P19.
- `tests/test_moderation.py`: P14 → P15 → P19.
- `tests/test_examples.py`: P7 → P9 → P10 → P11 → P14 → P16 → P18 → P19 → P20
  → P21 → P23.
- `tests/test_profiles.py`: P9 → P10 → P14 → P16 → P18 → P20 → P22 → P23.
- `pyproject.toml`: P9 → P10 → P14 → P16 → P18 → P20 → P23.

Where a chain link is not a direct dependency, P1…P26 run strictly in
sequence, so the order holds. The chains for `pyproject.toml`,
`tests/test_examples.py` and `tests/test_profiles.py` are also explicit
because each step there edits a running count the next one reads.

## Requirement coverage

| Req | Steps | Acceptance criteria |
|-----|-------|---------------------|
| R1 | P1, P2, P4, P5, P7, P18, P21, P22, P25 | AC1–AC7 |
| R2 | P4, P8, P9, P19 | AC8–AC12 |
| R3 | P10, P12 | AC13–AC19 |
| R4 | P11, P13 | AC20–AC23 |
| R5 | P1, P3, P6, P8, P14, P15, P19 | AC24–AC28 |
| R6 | P2, P6, P7, P16, P17, P18, P21 | AC29–AC33 |
| R7 | P18, P19, P20, P21 | AC34–AC39 |
| R8 | P22, P23, P24, P25, P26 | AC40–AC42 |

## Review findings addressed

| Finding | Where | Change |
|---------|-------|--------|
| F-P1 (blocker) absent delivery | P13 | Explicit first branch "0 delivery entries → `delivery: none`, no reply", ordered before the `confirmed` branch (which now requires a non-empty list); AC21 test for an absent delivery. |
| F-P2 (major) moderation concurrency | P14, P15 | Per-channel `asyncio.Lock` around check-and-reserve; counters reserved before the send, never rolled back; request outside the lock; P15 approvals and `auto_apply` use the same function and pop the proposal under the lock; `asyncio.gather` tests in both steps. |
| F-P3 (major) kick catalog assertion | P19, shared-file rule | `tests/test_examples.py` added to P19 Files; P19 rewrites the `chat.write` multiplicity assertion to {twitch, kick}; the chain now includes P19. |
| F-P4 (minor) memory argument contracts | Decision 6, P11 | Decision 6 separates `memory.recall` (`{}`) from `memory.record` (exchange data only, no identity property, `additionalProperties: false`); P11 tests both refusals. |

## Ordering rationale

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
