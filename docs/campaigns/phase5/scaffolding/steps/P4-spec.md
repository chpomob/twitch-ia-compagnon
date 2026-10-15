# Step P4 — Runtime guard

Plan step `P4` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: a module boundary, an ordering guarantee ("before any subscriber"), and a public context API change.
  - **`for_module`.** `RuntimeContext.for_module(module, *, owned_inputs=None, ownership=None)` takes keyword-only additions, so existing calls are unchanged.
  - **`ModuleContext.inputs`.** `ModuleContext` gains a field `inputs: OwnedInputs`, exposed read-only. When `for_module` gets no ownership, it holds `OwnedInputs.unrestricted()`, which answers `owns(...) == True` for every pair, so modules built by existing harnesses keep ingesting as today (P8 relies on this). It also holds a private `_guard`.
  - **The guard.** When `ownership` is given, the `bus`, `triggers` and `chat` properties return module-scoped facades (`_OwnedBus`, `_OwnedTriggers`, `_OwnedChat`). Every other attribute is delegated through `__getattr__`. When `ownership` is `None`, the shared objects are returned, so the 35 `for_module(...)` test sites keep identity.
  - **`_OwnedBus.publish(event_type, payload, metadata)`**, for `channel.chat.message` with `input_family_of_event(...) == (F, P)`:
    - If the module **declares** `input_families`: deliver only if `ownership.owner(F,P) == module`.
    - If the module is **undeclared** (R3 compatibility rule): deliver unless `(F,P)` has a resolved owner, or `inputs` set `(F,P)` to `none` explicitly.
    - **On rejection:** call the shared `supervision.count(COUNTER_INPUT_OWNERSHIP_REJECTIONS)`. If `(module, F, P)` is not yet in a set owned by the guard, add it and emit **one** trace `input.ownership.rejected` with `{module, family, platform}`. Then return without calling the bus, so no subscriber runs.
    - Other events, and events with no family (D1), pass through untouched.
  - **`_OwnedTriggers.evaluate`** applies the same predicate. On rejection it returns a refused `TriggerDecision` with reason `input_not_owned`, built with the existing dataclass at `core/triggers.py:366`, without counting (D4), and shares the diagnostic set.
  - **`_OwnedChat.append`** drops a non-owned event's append under the same rule.
  - **Loader wiring.** In `core/loader.py`, `_activate` and `_register_declarations` (the `for_module` calls at lines 583 and 1192) pass `owned_inputs=self.input_ownership.view_for(name)` and `ownership=self.input_ownership`.
  - **v1 route (D3, finding P1).** Where the loader calls a v1 `activate(bus, settings, catalog)` (the v1 branch of `_activate`, near `core/loader.py:515-522`), it passes `_OwnedBus(shared_bus, module=name, ownership=self.input_ownership, declared=False)` when `self.input_ownership.constrains_any()`, and the bare shared bus otherwise. The facade is the same class as the v2 one, constructed directly, so the predicate, counter and once-per-(module, family, platform) diagnostic are shared code. `_OwnedBus` is defined so that it can be built without a `ModuleContext`.
  - **`RUNTIME_API` is unchanged.** The additions are optional reads.

  Caller table (`grep -rn "for_module(" core modules tests`; blind spot: dynamic attribute access on the context):

  | File | Function/Method | Migration Note |
  |------|----------------|----------------|
  | core/loader.py:583 | `_activate` | Passes `owned_inputs`, `ownership` |
  | core/loader.py:1192 | `_register_declarations` | Passes the same, so a declaration-time facade sees the view |
  | core/config_ui/__init__.py:2963, 3720, 4023 | `result.for_module` (a different method on the Check result) | Unrelated name; no change |
  | tests/* (35 call sites in 22 files) | `context.for_module(name)` | None: no `ownership` → shared objects returned, identity preserved |
  | tests/test_loader.py:1345 | v1 route `received_bus is context.bus` | Unchanged and not adapted: that test enables one v1 module and no declarer, so `constrains_any()` is false and the bare bus is passed (D3) |
  | core/loader.py (v1 branch of `_activate`) | v1 `activate(bus, settings, catalog)` | Receives `_OwnedBus` when the table constrains any pair |

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC13, AC44, AC9
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

[`core/runtime.py`, `core/loader.py`, `tests/test_input_ownership.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2, P3]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_input_ownership.py`, with a real `EventBus`, recording subscribers standing in for the trigger engine, `users`, `chat_context` and brain, and a recording supervision:
  - **AC13.** A declared test module publishes 3 events for a pair it does not own. 0 reach any subscriber, the counter is 3, and exactly 1 `input.ownership.rejected` trace is emitted.
  - **AC44.** An undeclared module publishing 3 events for (`chat`, x):
    - with nobody declaring the pair, all 3 delivered, counter 0;
    - with a second module declaring `default: true`, 0 delivered, counter 3;
    - with `inputs: {chat: {x: none}}`, 0 delivered, counter 3.
  - **AC44 for a v1 publisher (finding P1).** The same three cases with a tmp **v1** manifest (no `manifest_version`) whose `activate(bus, …)` publishes the 3 events through the bus it received: 3/0/0 delivered and counter 0/3/3, with exactly 1 diagnostic in each rejecting case. In the first case the received bus `is` the shared bus.
  - `tests/test_loader.py` runs in full, unadapted.
  - **AC9.** The `twitch` stand-in's `context.inputs.pairs` is empty while the owner's view holds (`chat`, twitch).
  - `watch_tick` events and non-chat events pass untouched.
  - The evaluate facade returns a refused decision without counting.
  - `for_module(name)` without `ownership` still returns `bus is runtime.bus`.

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

- **Ordering.** The check must run before `bus.publish` begins its handler chain. The facade returns before calling the bus, so even an audit middleware with a negative order sees nothing, which satisfies "reaches no subscriber".
  - **Identity.** A module comparing `context.bus is ...` would differ. A grep of `modules/` finds none (P22 re-greps).
  - **v1 facade completeness.** A v1 module may call any bus method. `__getattr__` delegation covers them; `publish` is the only intercepted name. A test subscribes through the facade and receives an owned event.
  - The diagnostic set is bounded by modules × families × platforms. Platforms come from events, so the set is capped at a constant (e.g. 256 entries), after which the counter alone is incremented, still never per event.

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
