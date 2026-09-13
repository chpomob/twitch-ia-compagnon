# Phase 1 — agentic text + image vertical, local and remote

Archived campaign material for PHASE 1 of the project. The authoritative design remains
`docs/design-v2.md` (section 6 defines this phase and its exit criteria).

## Provenance (all pipeline artifacts are English, per the pipeline discipline)

| File | Origin | State |
| --- | --- | --- |
| `brief.md` | written by the orchestrator from design §6 + §3.1–3.4 + §4.3–4.4 | input to the spec stage |
| `spec.md` | adversarial spec v1.2 (8 requirements R1–R8, 58 acceptance criteria AC1–AC58) | approved: 5 challenge findings resolved AND the product-owner delivery revision independently verified |
| `plan.md` | adversarial plan v1.1 (24 ordered steps P1–P24) | 5 challenge findings addressed (verified against the plan text); P24 is the full-branch review gate |

The historical v1 MVP specification and plan are frozen under `../v1-mvp/` — they are NOT the
target of this phase and must not be overwritten by later steps.

## The one product decision this phase settles beyond the design

**Delivery is a configured, pluggable terminal step.** The run's delivery is a configured ordered
list of delivery actions, invoked only after the final answer exists: mode `fixed` (explicit list)
or mode `modules` (derived from the enabled modules that declare a delivery capability, with a
configurable preference order). Each entry declares how the answer text maps into its arguments (a
named argument, or none for an effect-only delivery such as a stream-scene change). Every entry runs
at the terminal step, each receiving the text only if declared, so `[chat.write, audio.say]` writes
and speaks. The model never selects the delivery, never performs an intermediate effect, and phase 1
ships the mechanism with a single `chat.write` entry — adding a delivery module must not require any
change to the agentic loop.

## Execution

Steps are run in dependency order by the adversarial code loop (one plan step = one focused step
spec), each with its own commit, green suite and reviewer approval; P24 reviews the whole branch diff
before delivery. Per-commit reviews cannot catch cross-file integration defects by construction —
that is what the final gate is for.
