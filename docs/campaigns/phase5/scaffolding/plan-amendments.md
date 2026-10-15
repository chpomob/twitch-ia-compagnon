# Phase 5 — amendments to the approved plan and specification (mid-campaign record)

This file exists so that a mid-campaign correction is **visible in the durable record** instead of
disappearing into a commit message. The closing gate (`gate-prompt-1.md`) must read it, and the phase
summary must carry it.

Everything below was found by the campaign itself, not by a later review: step P12's DEV **stopped
before editing any file**, as its instructions require, and reported the conflict. Its full report is
preserved verbatim at `docs/campaigns/phase5/scaffolding/loop-out/phase5-p12/01_build.json`; the loop
recorded `REJECT — EMPTY_DIFF` because the working tree was legitimately clean.

Both findings were re-checked independently against the code before acting (the keyword set in
`core/contracts.py`, the `companion_name` read in `core/loader.py` and the refusal in
`core/triggers.py:1342`). Both are accurate.

## D9 — the kernel's schema subset gains string length bounds

**What the plan assumed.** Step P12 declares the bridge's run action with

    argument_schema = {type: object,
                       properties: {action: {type: string, minLength: 1, maxLength: 128},
                                    arguments: {type: object}},
                       required: [action], additionalProperties: false}

because AC15 requires *that schema itself* to refuse an `action` of 0 or 129 characters.

**Why it was impossible.** `core/contracts.py` supports exactly `type, enum, minimum, maximum,
required, properties, items, additionalProperties` plus the annotations `title, description, default`.
Every other keyword is refused **on purpose**, so an unsupported constraint can never look enforced.
`ActionSpec.__post_init__` validates `argument_schema`, so the loader rejected the manifest at
declaration:

    SchemaError: argument_schema.properties.action.minLength: unsupported schema keyword

**Resolution — approved by the operator's standing decision, not by convenience.** A fix step,
`steps/P12F1-spec.md`, adds `minLength` and `maxLength` to the kernel's subset, additively: accepted
only as non-negative integers (`bool` excluded), `minLength > maxLength` refused, applied to strings
only, and **refused at validation on a node whose type is not a string** — the same stance that makes
the subset refuse unknown keywords. No module in the tree declares either keyword today, so no
existing validation outcome changes.

The alternative — moving the length check into the provider and rewording AC15 — was **refused**: the
brief's binding decisions state that a specification which narrows them is wrong, and AC15 is approved
wording. The kernel grows a capability rather than an acceptance criterion shrinking.

**Cost accounting.** `core/contracts.py` is already among the specification's five core targets, so
the file count does not change: the plan's row stays at **seven** core files (D5 and D6), and D9 is a
widening of what one of them accepts, not a new file.

## P12 amendment — the bridge must configure a companion name

**Why.** The plan's default trigger policy for the bridge is `any_of`: `event_kind: [cue]` OR
`keyword: ['${companion_name}']`. But `core/loader.py` reads `companion_name` from the module's own
settings, `core/main.py` builds the trigger registry without a fallback name, and
`core/triggers.py:1342` raises "uses ${companion_name} but no companion name is configured".

As the step was first written, **any profile enabling the bridge would have failed startup at trigger
registration** — directly contradicting the R1 standing decision that the bridge is optional and that
its presence never breaks the companion.

**Resolution.** The bridge's `settings_schema` declares an optional `companion_name` string setting
(with a `title`, no `default`, no `required`, so AC5 holds), and its `validate_settings` refuses a
missing or blank one with a value-free diagnostic naming the field — exactly as it handles `url` — so
`--check-config` reports it without opening a socket. This stays inside P12's own files.

## P12 amendment — three more assertions inside an already allowlisted test

The plan listed the adaptations of `tests/test_examples.py` as `MODULE_NAMES`, `EXPECTED_MANIFESTS`,
`DECLARATION_KEYS` and `DECLARED_ACTIONS`. The same test function also carries `SECRET_SETTINGS`
(which needs the bridge's `("password",)` entry) and the `len(declared) == 13` /
`declared == PROVIDED_ACTIONS["pc"] | {...}` assertions (which need `automation.action.run`). The
allowlist entry does not grow: it is the same test, adapted for the same reason.

## What the gate must verify about these amendments

- AC15 passes **with the schema as written**, and no other acceptance criterion's wording changed.
- The run action's manifest loads; a profile enabling the bridge without `companion_name` fails
  `--check-config` with a diagnostic naming the field, and **does not** fail at trigger registration.
- The kernel still refuses every keyword outside its subset, and refuses `minLength`/`maxLength` on a
  non-string node.
- The suite is green, the seven allowlisted tests are adapted (not weakened), and the four shipped
  profiles still have zero changed lines.

## D10 — a module's own health report is not overwritten by a successful phase observation

**Where it was found.** Step P14 stopped before editing anything (a second time in this campaign) and
reported that implementing the plan literally would leave the bridge **ready** with an unreachable
instance. Report preserved at
`docs/campaigns/phase5/scaffolding/loop-out/phase5-p14/01_build.json`.

**The defect, re-verified in the code.** `core/lifecycle.py`, `PhaseCoordinator._run_hook` ends with
`await self._observe(phase, name, failure, ...)` for every hook that returned, failing or not.
`core/runtime.py` maps `PHASE_START_INPUTS` to `MODULE_STATE_READY` in `_PHASE_STATES`, and the state
transition deduplicates on `(state, reason)`. So after a module reports
`(degraded, "unreachable")` itself, the coordinator's `(ready, None)` is a new fact: it publishes
`module.ready` and records the module ready. The comment above `_PHASE_STATES` reasoned only about a
module that never spoke.

**Why it matters beyond this step.** The plan's degraded report is how R1 (an unavailable instance
never breaks the companion) and R6 (the bridge is ready only while connected, authenticated and holding
a reconciled inventory) are honoured. AC3's literal wording — "exactly 1 `module.degraded` with
`unreachable`" — would still have passed, so the defect would have survived the step, its review and
probably the gate. That is the value of a DEV that stops instead of building on a broken invariant.

**Resolution, chosen to remove the class rather than the instance.** New step `steps/P14F1-spec.md`:
`core/runtime.py` reports a phase's state only when the module has not reported one itself in this
activation; a module-authored state stands until the module reports another one, or until the module is
closed (`PHASE_CLOSE`/`PHASE_CLOSE_OBSERVATION` still report `stopped`). The reporter proposed
suppressing the observation for `PHASE_START_INPUTS` alone; the general rule also covers `prepare`,
where the same clobbering exists.

**Why it is safe.** A grep of `modules/` for `supervision.degraded` or `supervision.ready` returns
**0 hits**: no shipped module reports a state today, so no existing behaviour depends on the
overwrite. The ordinary lifecycle records (`prepare` → ready, `start_inputs` → ready, `close` →
stopped) are unchanged for every module that reports nothing, and the phase-0 to phase-4 lifecycle
tests must stay green.

**Cost accounting.** `core/runtime.py` is already among the specification's five core targets; the
file count stays at seven (D5, D6). D10 changes when one of them reports a state.

**P14 amendment.** The step now depends on P14F1, and its AC3 test must assert the **final state**
(degraded after the startup budget, with no `module.ready` after the `module.degraded`) — the record
count alone is not the criterion.
