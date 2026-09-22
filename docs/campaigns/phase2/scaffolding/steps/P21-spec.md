# Step P21 — Full-branch review gate before PR delivery (R1–R10; AC7-diff, AC20, AC25-grep, AC35-grep, AC40-unmodified, AC41-two-subtractions)

Plan step `P21` of the approved Phase 2 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase2/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase2/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Review every changed file of the branch **together**, not per commit: per-commit reviews miss cross-file bugs by construction — each step is correct in isolation (P1 defines the part, P2 checks its lease, P3 adopts the late record, P10 leases and stamps it, P7 encodes it, P12 carries it across the proxy) yet the set only holds when the same `run_id`, the same store instance, the same clock and the same `expiry` arithmetic flow across `core/contracts.py`, `core/actions.py`, `modules/audio_input`, `modules/audio_output`, `modules/brain`, `modules/proxy`, `modules/agent_link` and `tests/conftest.py`; component decomposition ≠ integration. The gate walks: (1) `git diff --name-only <base>..HEAD` against the spec's `targets` list — every changed file is a target or under `docs/campaigns/phase2/`, and each plan `Files:` entry is a target; (2) `git diff <base>..HEAD -- modules/brain/` contains R4 hunks only (decision 5 sites) and no hunk in `_resolve_delivery`, `_deliver_all`, `_loop` (AC7); (3) `git diff <base>..HEAD -- core/actions.py` contains the P2 lease hunks and the P3 adoption hunks only, and `git diff <base>..HEAD -- tests/test_actions.py` leaves `test_a_confirmation_landing_after_the_deadline_is_never_a_success` and `test_a_confirmation_landing_in_time_survives_a_late_adoption` byte-for-byte unchanged (AC40); (4) AC41: `grep -rniE "adoption_lead|lead_seconds|deadline_margin|early_stop|deadline_lead"` over `modules/audio_output/`, `modules/audio_input/`, `modules/stream_control/` matches 0 lines, the three settings schemas declare no lead/margin key, and `git diff <base>..HEAD -- modules/` contains no expression subtracting a literal or a setting from the call deadline other than the exactly two AC41 authorizes by name, both in `modules/audio_input/`: the `capture_too_long` admission check (`(expiry − now) − grace_seconds`, evaluated once before any spawn) and the transcription window (`(expiry − now) − 1 s`, R3's `skipped:deadline` reserve, P11 — round 2 P1, round 3 V4), whose sole purpose is that the capture's `success` record is stamped before `expiry` and which bounds the transcription request only; the gate confirms that neither moves the recorder's kill from `expiry` (AC39) and that no subtraction of any kind appears in `modules/audio_output/` or `modules/stream_control/` (a third one anywhere, whatever its name, is a defect); (5) the caller tables of the spec and of this plan against `grep -rn` of the final tree (every `PART_TYPE_IMAGE_REF` site has its `audio_ref` twin, every `IMAGE_CONTENT_TYPES` site outside `modules/capture` reads `ATTACHMENT_CONTENT_TYPES`, every `RuntimeContext(` construction still builds); (6) each requirement R1–R10 against the step that claims it and the test that proves it (AC1–AC42 mapped below); (7) the eight allowlisted tests are the only phase 1 assertions changed; (8) `grep -rn` for `poll`, `twitch`, `obs`, `audio`, `capture`, `pipewire`, `aplay`, `ffmpeg` in `core/` and `modules/brain/` finds only the allowed sites; no model literal, no positive sleep; (9) the 11 fake-done shortcuts over the whole diff; (10) `pytest -q` green on the full suite, `--check-config` exit 0 on the three profiles, the phase 1 two-process test passing unchanged, and the six trial rows of the README naming the gate commit. Findings are fixed in place in the file that owns the rule, with a test, and recorded in a gate findings table in `docs/README.md` and the campaign README.

## Requirements

Plan mapping: R1, R10
Acceptance criteria owned by this step: AC7, AC20, AC25, AC35, AC40, AC41
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

[`docs/README.md`, `docs/campaigns/phase2/README.md`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P20]

All dependencies are already merged into `main` when this step runs.

## Tests

The full suite; the gate's own checklist; `git diff <base>..HEAD --stat` reviewed against the target list (every target touched or recorded as untouched, nothing outside it except `docs/campaigns/phase2/`).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (integration). Likely cross-step findings: the executor's `expires_at` versus a module's `expiry` (both `min(call.deadline, entered + spec.timeout_seconds)` but stamped on two clocks in the proxy case), a module's `CancelledError` handler awaiting something the cancellation interrupts again (the record then never reaches the executor and the generic record appears — visible as a `timed out with emission` message in an AC38/AC39 run), the proxy translating `transcription` verbatim while the brain expects the contract's field names, the fixture platform's poll service and the twitch service disagreeing on the `sent` flag. Each is fixed where the rule lives before delivery.

## Requirement coverage

| Requirement | Steps | Acceptance criteria |
|-------------|-------|---------------------|
| R1 | P8, P9, P17, P18 | AC1, AC2, AC3, AC4, AC5 (AC6 via R2, AC35 playback, AC38 module half) |
| R2 | P17 | AC6, AC7 (diff clause at P21) |
| R3 | P10, P11, P18 | AC8, AC9, AC10, AC11, AC12, AC42 (AC39 module half, AC41 transcription window) |
| R4 | P1, P2, P6, P7 | AC13, AC14, AC15, AC16, AC17 |
| R5 | P1, P12, P18 | AC18, AC19, AC20, AC35 |
| R6 | P14, P15 | AC21, AC22, AC23, AC24, AC36 |
| R7 | P4, P13, P16 | AC25, AC26, AC27, AC28, AC37 |
| R8 | P9, P11, P14, P16, P19 | AC29, AC30, AC31 |
| R9 | P8, P10, P14 (package data), P12 (protocol addendum), P19, P20 | AC32, AC33, AC34 |
| R10 | P3 (executor), P9, P10, P14, P16 (module halves, no lead), P11 (transcription window), P20 (README), P21 (AC41 grep and two-subtraction check, AC40 diff) | AC38, AC39, AC40, AC41, AC42 |

## Caller enumeration

The spec's four tables (`ActionObservation.parts`/content-type set,
`PROBE_REASONS`/`capabilities.required`, `RuntimeContext.services`, manifest
catalogue 8 → 11) stand as written and are applied by P1, P2, P6, P7, P12
(parts and content types), P1, P6 (probe vocabulary), P4, P5, P13, P14, P16
(services), P8, P10, P14, P19 (catalogue). This plan adds the API changes its
decisions introduce:

### `core/actions.py::_run_invocation` adoption rule (P3) — R10

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/actions.py | `_run_invocation` (provider-wins-late branch, timer-wins branch) | Adopt an interruption record through `_validate_observation`; generic record otherwise. Signature unchanged. |
| core/actions.py | `_validate_observation`, `_reject`, `_terminate_uncertain`, `_publish` | Unchanged; called on the new path. |
| modules/brain, modules/proxy, modules/agent_link, modules/twitch, modules/capture | callers of `executor.invoke` and phase 1 providers | No change: a phase 1 provider never returns a record after its deadline, so its outcomes are identical. |
| modules/audio_output, modules/audio_input, modules/stream_control | new providers (P9, P10, P14, P16) | Return their own interruption record at the deadline (watcher or `CancelledError` handler); `mark_not_emitted()` for local playback, `mark_emitted()` before a scene/poll command. |
| tests/test_actions.py | phase 1 boundary tests (lines 567, 595, 651) | Unchanged; new cases beside them. |
| Unknown | external providers returning after their deadline | None known; the change only widens what is adopted for `timeout`/`cancelled`/`error`. |

### `ServiceRegistry`, `RuntimeContext.services=None`, `ModuleContext.services` (P4)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/main.py | `_assemble_runtime` | Pass `services=ServiceRegistry()`. |
| core/runtime.py | `RuntimeContext.__post_init__`, `for_module`, `ModuleContext` | New optional field, facade, surface check. |
| tests/conftest.py | `runtime_context` | New `services` parameter, registry by default (P5). |
| tests/test_brain.py (2), tests/test_loader.py (1), others (2) | direct `RuntimeContext(` constructions | No change (optional field); a module activated on one gets a facade with `available` false and publishes nothing (P13 guard, round 2 P2). |
| modules/twitch/__init__.py | `activate` | Publish only when `context.services.available` (a bound registry), never on the facade's presence; a default context publishes nothing (P13). |
| tests/fixtures/modules/fakeplatform/__init__.py | `activate` | Publish the scripted service under the same `available` guard (P13). |
| modules/stream_control/__init__.py | `prepare` | `entries()` then `resolve()` per platform — the three-method surface only, no concrete-class check (P16, round 2 P3). |
| Unknown | external embedders building `RuntimeContext` | None known; additive. |

### `core.contracts` gains `CAPABILITY_STRUCTURED_OUTPUT`, `CAPABILITY_VISION`, `CAPABILITY_AUDIO` (P1); the brain imports them (P6)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/brain/__init__.py | `KNOWN_CAPABILITIES`, `_validate_capabilities`, `probe`, `_propose` | Import from `core.contracts`; the brain's names kept as re-exports so test imports keep working. |
| tests/test_model_adapter.py, tests/test_brain.py | imports of the brain constants | No change (re-exported). |

### `_ModelAdapter._probe_reason(kind=)`, `_resolve_attachment`, `_Transcript.attachment_parts`, `_expired_attachment` (P6, P7)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/brain/__init__.py | `probe`, `_verify`, `_encode_part`, `_loop`, `_propose`, `_discard_images`, run-end release | All call sites updated in the same step. |
| tests/test_model_adapter.py, tests/test_brain.py, tests/test_agentic_loop.py | any private-name reference | Grep before renaming; an existing reference keeps a thin alias. |

### `ActionExecutor._reject_parts` / `_discard_images` cover `audio_ref` (P2)

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/actions.py | `_validate_observation`, `_reject`, `_reject_parts`, `_discard_images` | Both reference types; names kept. |
| modules/brain, modules/proxy, modules/agent_link | callers of `context.executor.invoke` | No signature change. |

### Provider deadline convention (decision 1) — new providers only

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| modules/audio_output/__init__.py | `_invoke_speak`, `_invoke_play`, `drain` | `mark_not_emitted()` at entry; watcher at `expiry`; `CancelledError` caught → stop → record returned (`timeout`/`playback` at or after `expiry`, `cancelled` before or on drain); `stop_grace_seconds` after the stop. |
| modules/audio_input/__init__.py | `_invoke_capture`, `drain` | `grace_seconds` is the admission reserve only; watcher at `expiry` kills; `CancelledError` caught → kill → `error capture_timed_out` at or after `expiry`, `cancelled` before or on drain — while recording only; after `attachments.put` no interruption record: the transcription request is bounded by the transcription window `(expiry − now) − 1 s` (R3's `skipped:deadline` reserve, the second subtraction AC41 authorizes); attempt → decide on the clock at the wake → author and return with no further await, so the `success` is stamped at the decision instant, `< expiry` for any wake delayed by less than the reserve; window `≤ 0` at entry → `skipped:deadline`; bound, drain or `CancelledError` there end `success` + `failed:stt_timed_out` (P11, round 2 P1, round 3 V1). |
| modules/stream_control/__init__.py | `_invoke_scene_set`, `_invoke_poll_create`, `drain` | `mark_emitted()` before the command; reads bounded by `expiry − now`; own `timeout` (cause `confirmation_lost`) at the deadline, resolved `external_unknown` by the emission rule. |
| modules/capture, modules/twitch (phase 1) | unchanged | Keep their phase 1 behaviour. |

## Test failure allowlist handling

The eight tests the spec allowlists are rewritten in P19 only:
`tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities`
(11 manifests, `EXPECTED_MANIFESTS` extended with the three new declarations),
the three `test_profile_enables_its_modules_and_contains_no_literal_credentials[*]`
and the three `test_profile_grants_exactly_its_provided_action_set[*]` cases
(R9 lists), and
`tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`
(`== 11`). Between P8 and P19 these eight tests are red by construction (the
new directories exist before the profiles change); every other test stays
green at every step, which the gate verifies per step from the commit log.
The two phase 1 deadline tests of `tests/test_actions.py` are not on the
allowlist and are not modified (AC40).

## Ordering rationale

Contracts before consumers: P1 (part vocabulary, capability and probe names)
changes `core/contracts.py`, which every later file imports; P2 makes the
executor enforce the audio lease before any provider produces one; P3 amends
the executor's adoption rule (R10) before any module relies on it — the three
new modules (P9, P10, P14, P16) return interruption records at the deadline
and would be cut to the generic record without it, so P3 precedes every
module step that tests a deadline through the real executor. P4 is an
independent core seam (service registry) needed by the platform modules
(P13) and `stream_control` (P16); it goes first so the shared harness (P5)
can build a registry by default. P5 upgrades the harness before the brain and
the modules change shape, because every new suite reads it. The brain follows
in two layers — the probe and capability name (P6), then the call-time
encoding, omission rule and estimate (P7) that need both the probe's verified
set and the executor's lease check. `audio_output` (P8, P9) needs contracts,
the harness and P3, and ships first among the modules because the delivery
proof (P17) and the scenario (P18) speak through it; `audio_input` (P10, P11)
needs the executor's audio lease (P2) and R10 (P3) and follows P8 so the
three `pyproject.toml` edits are serialised (P8 → P10 → P14). The proxy path
(P12) needs the vocabulary and the harness only and precedes the scenario.
The platform poll services (P13) precede `stream_control`'s scene step (P14)
and its polls step (P16), and P14 precedes the websocket provider (P15)
because the provider implements the boundary P14 defines; P16 follows P15 so
the two edits of `modules/stream_control/__init__.py` are ordered. P17 proves
R2 once the two delivery-capable modules exist and the brain's R4 hunks are
in; P18 integrates every module across both transports and must follow all
of them, P15 included (its scene-change shutdown case drives the websocket
provider). Profiles and the examples/profile suites (P19) need every module
and the scenario's seams; the README records the trials and the hygiene suite
greps the finished tree (P20). P21 is the full-branch gate: it reviews the
integrated set that no per-step review could see.

**Shared-file rule (finding P3).** Two steps that modify the same file are
ordered by an explicit dependency edge, so a dev loop never applies two
unordered edits to one file. The pairs and their edges: `core/contracts.py`
is P1 only; `core/actions.py` P2 → P3; `tests/test_observations.py` P1 → P2;
`tests/test_actions.py` P3 only; `tests/test_model_adapter.py` and
`modules/brain/__init__.py` P6 → P7; `modules/audio_output/*` and
`tests/test_audio_output.py` P8 → P9; `modules/audio_input/*` and
`tests/test_audio_input.py` P10 → P11; `pyproject.toml` P8 → P10 → P14;
`tests/test_stream_control.py` P4 → P13 → P14 → P15 → P16;
`modules/stream_control/__init__.py` P14 → P15 → P16; `docs/README.md`
P20 → P21; `tests/conftest.py` is P5 only, `tests/test_proxy.py` P12 only,
`tests/test_delivery.py` P17 only, `tests/test_phase2_scenario.py` P18 only,
the three profiles and `tests/test_examples.py`/`tests/test_profiles.py` P19
only, `tests/test_hygiene.py`/`tests/test_phase2_trials.py` P20 only. P18
names P15 directly; P19–P21 reach P15 through P18 → P16 → P15. The sequence
`P1..P21` satisfies every edge (each step depends on lower ids only).

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
