# Step P4 — Bounded runtime service registry and its module-scoped facade (R7; AC25)

Plan step `P4` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Add `ServiceRegistryError(RuntimeContextError)` and `ServiceRegistry(max_entries=64)`: `publish(kind, platform, service, *, module)` requires non-empty text `kind`/`platform`/`module` and a non-`None` service, refuses a key already published (`"service (<kind>, <platform>) is already published by module '<a>'; module '<b>' cannot publish it"`), refuses the entry beyond `max_entries` (`"service registry is full (64 entries)"`), records `(kind, platform) → (module, service)`; `resolve(kind, platform) → service | None`; `entries() → Mapping[(kind, platform), module]`. `RuntimeContext` gains `services: Any = None`, surface-checked for `publish`/`resolve`/`entries` when present — the one accepted interface, the three calls the facade delegates and the only ones a consumer may make (round 2, P3); `for_module` builds `ModuleServices(self.services, name)`; `ModuleContext` gains the `services` field and facade (`available` → `True` iff the context carries a registry, so a publisher tests it instead of provoking the error — round 2, P2; `publish(kind, platform, service)` under the bound module name, or `RuntimeContextError("runtime context: no service registry")` when absent; `resolve` → `None` when absent; `entries()` → empty when absent). `core/main.py::_assemble_runtime` passes `services=ServiceRegistry()`. The words `poll`, `twitch`, `obs` appear nowhere in either core file; `__all__` and the module docstring describe "a bounded key→service table the core does not interpret".

## Requirements

Plan mapping: R7
Acceptance criteria owned by this step: AC25
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

[`core/runtime.py`, `core/main.py`, `tests/test_stream_control.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_stream_control.py` (created here, section "service registry"): publish then resolve returns the same object; `resolve` of an unpublished key is `None`; a second publication of one key raises naming both modules; the 65th entry is refused and the 64th accepted; `RuntimeContext(services=object())` and `RuntimeContext(services=<publish and resolve only>)` are each refused naming the field `services` and the missing method — `entries()` for the second (round 2, P3); `for_module("m").services.publish(...)` records module `m`; a context without `services` yields a facade with `available is False`, `resolve(...) is None`, `entries() == {}` and a `publish` raising `RuntimeContextError`, and one with a registry `available is True` (round 2, P2); through `ModuleLoader` with two temporary modules whose `activate` both publish `("poll", "fake")`, activation fails with a diagnostic naming both module names; a file-read assertion that `core/runtime.py` and `core/main.py` contain the literal `poll` 0 times and none of `twitch`, `obs`. The 5 direct `RuntimeContext(` constructions in tests and the one in `core/main.py` are unchanged (optional field).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (module boundary: a new shared collaborator). The frozen dataclass keeps `kw_only`; `_require_surface` demands exactly `publish`, `resolve`, `entries` — the three calls the facade delegates and the only ones a consumer (P16) may make; a fake registry implements all three and there is no two-method form (round 2, P3); the loader wraps the activation exception as any other, so the diagnostic must carry no configuration value.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase2): ...`, `fix(phase2): ...`, `test(phase2): ...`, `refactor(phase2): ...`).
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
