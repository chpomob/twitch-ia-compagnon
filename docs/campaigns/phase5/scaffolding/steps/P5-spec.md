# Step P5 — Family-gated binding

Plan step `P5` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial: a public registry API change (D5, deviation flagged).
  - **`core/actions.py`:**
    - `ActionBinding` gains `excluded_platforms: frozenset[str] = frozenset()`.
    - `ActionRegistry.bind(..., excluded_platforms: Iterable[str] = ())` and `register(..., excluded_platforms=())` pass it through.
    - The overlap test in `bind` and the containment test in `resolve` treat a binding with `platform == '*'` as not covering any excluded platform.
    - Everything else is unchanged.
  - **`ModuleActions.bind`/`register` in `core/runtime.py`.** When the action name is the value of `INPUT_FAMILY_GATED_ACTIONS[F]` and the module context carries an ownership table, the facade rewrites destinations from the spec's declared scope. The rule never emits a concrete destination that a wildcard binding of the same module already covers (finding P2):
    - **If the module owns `(F,'*')`:** exactly one `Destination('*','*',scope)` with `excluded_platforms = ownership.excluded(F)`, and **no** concrete destination. Concrete rows it also owns (for example `viewers.kick` and `viewers.youtube`, which exist in the domain because the bridge declares them, and resolve to the `*` owner by D2 step 4) are already served by the wildcard, because they are not in `excluded(F)`.
    - **Otherwise:** one `Destination(P, '*', scope)` per concrete platform P the module owns for F.
    - `excluded(F)` is exactly the concrete platforms in the domain whose owner differs from the `(F,'*')` owner, including platforms set to `none` or unowned. So each concrete platform is served by at most one binding: the wildcard owner's when it is not excluded, its own owner's concrete binding when it is.

    If the module owns no pair of F, `register` only **declares** the spec and binds nothing. Two declarers of the same spec merge destinations, per the P19-era fix noted in memory "chat-write second-declarer conflict".

  Caller table (`grep -rn "\.bind(\|\.register(\|ActionBinding(" core modules`; blind spot: providers bound through helper indirection):

  | File | Function/Method | Migration Note |
  |------|----------------|----------------|
  | core/actions.py:686 | `ActionRegistry.bind` | New keyword, default `()` |
  | core/actions.py:755 | `ActionRegistry.register` | Passes the keyword through |
  | core/actions.py:910 | `ActionRegistry.resolve` | Honours exclusions |
  | core/actions.py (1 site) | `ActionBinding(...)` | New defaulted field |
  | core/runtime.py | `ModuleActions.bind/register` | Rewrites destinations for gated actions only |
  | modules/*/__init__.py (every `self._actions.register/bind`) | — | Unchanged; only `users.read` registrations (users, bridge) take the gated path |

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC14
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

[`core/actions.py`, `core/runtime.py`, `tests/test_input_ownership.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4]

All dependencies are already merged into `main` when this step runs.

## Tests

- **AC14 with stand-ins, in the exact shipped shape.** Two test modules both register `users.read`. One declares (`viewers`, `*`) `default: true`; the other declares (`viewers`, [twitch, kick, youtube]) `default: false`, so the domain holds concrete kick and youtube rows that resolve to the `*` owner. With `inputs: {viewers: {twitch: <second>}}`: preparation raises no `AmbiguousBindingError`; the `*` owner holds exactly 1 binding (wildcard, excluding `{twitch}`) and the second exactly 1 (twitch). A call at twitch invokes only the second; calls at kick and at youtube invoke only the `*` owner (counted by `executor.provider_invocations` and per-provider spies).
  - **Non-overlap invariant.** For each of these resolved tables — no `inputs`; `twitch → second`; `twitch → second, kick → none`; `'*' → second` (second declaring `*` too); the second owning nothing — collect the bindings the facade produced and assert they are pairwise non-overlapping by the registry's own overlap test, and that every platform in the domain plus one undeclared platform resolves to at most one binding.
  - **Registry unit tests:** an excluded platform is not overlapped; `resolve` returns `None` for an excluded platform with no other binding; existing overlap refusals are unchanged when no exclusion is given.
  - A module owning nothing declares but does not bind.
  - Run `tests/test_actions.py` in full.

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

- `excluded_platforms` must never be reachable from module code; it is set only by the facade. Assert this by grepping `modules/` in P22.
  - A gated action registered **before** ownership exists (a test harness) keeps today's behaviour.
  - **`none` under a wildcard owner.** `inputs: {viewers: {kick: none}}` puts kick in `excluded(viewers)`, so a kick read resolves to no binding and the executor reports its existing no-provider outcome. This is intended (the operator set the pair unowned) and is tested in the invariant list.

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
