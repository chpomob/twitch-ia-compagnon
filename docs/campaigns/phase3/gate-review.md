# Phase 3 — P26 full-branch review gate

Scope: the whole phase 3 branch diff, read file against file rather than
commit by commit. Authority: `spec.md` (R1–R8, AC1–AC42), `plan.md`
(P1–P26), `docs/design-v2.md`.

- **Base commit:** `2d4893f` (the parent of `399c766`, the first phase 3
  commit; phase 2 was delivered at `2165383`, and `2d4893f` only adds the
  research report phase 3 cites as input).
- **Head:** `18ac3ce` (P25).
- **Diff:** 93 files, +39 849 / −168.

## Verdict

**REQUEST_CHANGES.**

The suite is green, the AC39 core scan, AC40 and AC42 hold, and every AC has
a passing test on the real module it names. Two cross-file contracts are
still broken:

- **G1 (major):** notices with no text never reach a route. This affects
  every Kick notice, the Twitch `follow`, and YouTube new members and gifts.
- **G2 (major, escalated):** AC39's poll half (`platform_unsupported`) is
  not implemented, and its module is not a spec target.

Findings G3–G7 are minor or need a decision above the steps. Fixes loop back
to the step named in each finding.

## Checklist

| # | Check | Result | Evidence / command |
|---|-------|--------|--------------------|
| 1 | The diff touches only spec targets and `docs/campaigns/phase3/` | **FAIL (escalated, G4)** | `git diff --name-only 2d4893f..HEAD` compared with the spec `targets:` list. Every target is touched, and `.gitignore` is permitted. Five files outside the targets were changed: `core/actions.py` (P9, P19), `core/admission.py` (P17), `modules/users/__init__.py` (P5, docstring only), `tests/test_actions.py` (P9, P19) and `tests/test_stream_control.py` (P8). |
| 2 | AC42: the three phase 2 profiles, `docs/design-v2.md`, `docs/proxy-protocol.md` and root `spec.md`/`plan.md` are unchanged | PASS | `git diff --stat 2d4893f -- config.yaml.example config.server.yaml.example agent.yaml.example docs/design-v2.md docs/proxy-protocol.md spec.md plan.md` gives 0 lines. No test pins the byte-identity (G7). |
| 3 | AC39 scan: a case-insensitive word grep of `core/` | PASS for the AC wording | `grep -rniw --include='*.py' <name> core/`: `kick` 0, `youtube` 0, `clips` 0, `viewer_memory` 0, `watch` 0. `twitch` 1 and `moderation` 1 are unchanged since the base and generic: `core/__init__.py:1` is the package docstring "Twitch AI companion", and `core/triggers.py:284` says "the moderation list". Phase 3 added no hit. |
| 4 | AC40: pyproject has 17 `modules.<name>` lines and only `aiohttp` and `PyYAML` | PASS | `pyproject.toml:11-12` and `:38-54`. `tests/test_profiles.py:1225-1227` and `tests/test_examples.py:607` assert 17. The clean-install half is skipped here because the venv has no pip, the same environment skip as in phase 2. |
| 5 | Full suite, 0 positive-duration sleeps | PASS | `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` gives **2422 passed, 18 skipped** in 47.9 s. Phase 2 was 1891/12, so +531 passed and +6 skipped, and the 6 extra skips are the opt-in `tests/test_phase3_trials.py` trials. The other 12 are 5 Linux-only `test_capture` cases, 6 phase 2 trials and 1 clean install without pip. `test_hygiene::test_ac45_the_suite_makes_no_positive_duration_sleep_call` passes. |
| 6 | Every requirement row has a passing test on the real module | PASS, with gaps G2 and G7 | See the requirement map below. The cited tests (152 node ids) give 248 passed and 1 skipped (the pip install). |
| 7 | No permanent-ban request can be built | PASS | Across `modules/`, "permanent" appears only in comments. Moderation requires `duration_seconds` and bounds it to 1..`max_timeout_seconds` (`modules/moderation/__init__.py:594-597, 1313-1316`). Each platform refuses a missing or ≤ 0 duration with 0 requests and always sends a duration: twitch `modules/twitch/__init__.py:932-943`, kick `modules/kick/__init__.py:961-980`, youtube `modules/youtube/__init__.py:988-1003` (`BAN_TYPE_TEMPORARY` only, `:263`). |
| 8 | No credential appears in traces | PASS | Secret scans over trace texts: `tests/test_clips.py:733-734, 896-897`, `tests/test_moderation.py:1210-1212` (twitch credentials), `tests/test_kick.py:947-949`, `tests/test_youtube.py:375-377, 878-881` (the latter includes the refreshed access token). The new `PROVENANCE_TRACE_FIELDS` path (`core/actions.py:1803-1811`) only adds scalar fields and still passes through runtime redaction (`core/runtime.py:522-528`). |
| 9 | Every caller-table row was migrated | PASS, one row inaccurate | See the caller tables below. The `tests/test_stream_control.py` row said "unaffected". It was affected (P8), and the two registry equalities were rewritten to the exact three-entry value, which is not a weakening. |
| 10 | Every flagged deviation is resolved or escalated | Escalated here | See the deviations section below. The open items are G2–G7. |

## Cross-file contracts (the whole-branch read)

| Contract | Result | Evidence |
|----------|--------|----------|
| Notice `kind`: the platforms write it; triggers, routes and command consumers read it | **BROKEN: G1** | All four producers put the kind at `payload.kind`: twitch `:418-420`, kick `:745-746`, youtube `:762-763`, fakeplatform `:844-846`. `core/triggers.py:1106` and the brain (`modules/brain/__init__.py:4852`) read it there. Commands ignore every kind but `message`: moderation `:896-897`, watch `:593-594`, viewer_memory `:1134-1135`. A route checks the kind before the command token (`brain :1291-1295`). The empty text of a notice breaks the brain (G1). |
| Brain principal vs `presence.yaml.example` rules | CONSISTENT | `run_principal` gives `brain.watch` only for `watch_tick` (`modules/brain/__init__.py:339-346`). Every route delivery and offered action of a chat run has a `brain` rule (`presence.yaml.example:406-538`). `chat.write` and `screen.capture` have `brain.watch` rules (`:428-429, :440-441`). |
| `conversation_id` from the brain, parsed by `viewer_memory` | CONSISTENT | The brain passes `SessionKey.serialize()` (length-prefixed, `core/contracts.py:937-945`). `parse_conversation_id` re-serializes and refuses a non-canonical form (`modules/viewer_memory/__init__.py:1155-1190`), and refuses a destination mismatch (`:1010-1011`). The file name is a sha256 of the key. All three platforms refuse `:` in author ids. |
| `moderation` service: 3 platforms + fake vs `moderation` | CONSISTENT | They share the signature `apply(operation, *, channel_id, message_id, target_author_id, duration_seconds, reason)` (moderation `:1376-1383`, twitch `:915-924`, kick `:945-954`, youtube `:959-968`). Outcomes `ok`/`rejected`/`rate`/`uncertain` are mapped in `moderation :1495-1518`. Kick offers `{timeout}` only (`:181`), and moderation refuses a delete there with `platform_unsupported` and 0 requests (`:1325-1326`; `test_moderation.py:1267`). |
| `clip` service: twitch + fake vs `clips` | CONSISTENT surface; **G3** in platform discovery | `create`/`lookup` and the outcome strings match (clips `:98, 112-137, 431-437`; twitch `:694-698, 814-873`). Kick and youtube publish moderation only (`test_kick.py:1101`). |
| `model_proposable`: loader → contract → brain | CONSISTENT; **G5** for lending | Only `modules/moderation/module.yaml:190` sets it true. Loader `core/loader.py:198, 1776`; contract refusals `core/contracts.py:1135-1144`; brain offer gate `modules/brain/__init__.py:3745-3763, 4901-4908`. The executor re-checks authorization (`core/actions.py:1231-1244`). |
| Unbound-capability reasons: `clips` vs `stream_control` | **BROKEN: G2** | Clips names kick and youtube `platform_unsupported` for each platform (`modules/clips/__init__.py:131, 370-373, 494-505`). stream_control has only "no poll service published" (`modules/stream_control/__init__.py:217, 877, 920-921, 944`), and only when no platform publishes one. |

## Findings

### G1 (major) — a notice without text is rejected by the brain; owner P5 (tests P7, P18, P21, P22)

- **Where:** `modules/brain/__init__.py:4849` requires a non-blank `text`
  (`_is_text`, `:4911-4912`).
- **Which notices are affected.** Several producers always emit `text=""`:
  - the Twitch `follow` (`modules/twitch/__init__.py:2641`);
  - every Kick notice (`modules/kick/__init__.py:809, 822`);
  - YouTube notices with no viewer comment, which includes every new member
    and every gift (`modules/youtube/__init__.py:824-833`).
- **Reproduction:**
  `_message_of_event({"payload": {"platform": "twitch", "channel_id": "1", "message_id": "m1", "text": "", "kind": "follow", "author": {"id": "42"}}})`
  raises `ValueError`. The same event with any non-empty text parses to
  `kind == "follow"`.
- **Effect.** The notice passes the `event_kind` trigger and is admitted.
  The run then ends `status=error`, `correlation={"failure": "malformed_work"}`
  (`:2793-2801`). When the brain owns the scheduler, the notice is dropped
  as "malformed chat message" (`:2723-2729`).
- **Why it matters.** The shipped `thanks` route
  (`presence.yaml.example:259-273`) can never thank a follower, any Kick
  subscriber or gifter, or a YouTube new member. R1(b) says a notice is
  "admitted exactly like a message".
- **Why the suite missed it.** `tests/test_presence_pack.py` only drives a
  raid, which carries a system message.
- **Fix:**
  - The brain accepts an empty text when `kind` is one of
    `PLATFORM_NOTICE_KINDS`.
  - Add a test that drives an empty-text notice end to end on each real
    platform (twitch follow, a kick sub, a youtube new member) and in the
    presence pack.

### G2 (major, escalated) — AC39 poll `platform_unsupported` is not implemented; owner P21, needs a spec amendment

- **What AC39 requires:** `stream.poll.create` unbound for kick and youtube
  with reason `platform_unsupported`.
- **What the code does:** stream_control only collects `(poll, *)` entries
  (`modules/stream_control/__init__.py:944`). Its only reason is "no poll
  service published" (`:217`).
- **What the test checks:** `tests/test_youtube.py::test_ac39_three_platforms_on_one_runtime`
  asserts only that polls are bound to twitch and that kick and youtube calls
  end `no_provider`.
- **Why the steps could not fix it:** `modules/stream_control/__init__.py`
  is not in the spec `targets`, so no step could fix it within scope. P21
  flagged it for this gate. The README records it (`docs/README.md:742-746`).
- **Needs an operator decision:** either add stream_control to the targets
  (a fix step owned by P21's line), or amend AC39 to accept `no_provider`
  for polls.

### G3 (minor) — `clips` treats every service-registry scope as a platform; owner P9 (test P22)

- **Where:** `modules/clips/__init__.py:424-430` adds the scope of *every*
  registry entry to the enabled platforms, whatever its kind.
- **Effect.** In the presence pack the brain publishes its scheduler service
  under `(admission, runs)`. Clips then emits a `module.degraded` naming a
  platform `runs` as `platform_unsupported`.
- **The test tolerates it.** `tests/test_presence_pack.py:559-566` allows it
  and names the defect in a comment.
- **Fix:**
  - Clips counts as a platform only a scope published under a platform
    service kind (`clip`, `moderation`, `poll`), or the platforms of the
    enabled platform inputs.
  - Tighten the presence-pack assertion to `set(by_module) == {"audio_output"}`.

### G4 (minor, escalated) — five files changed outside the spec targets

| File | Step | Change |
|------|------|--------|
| `core/actions.py` | P9 | Generic `PROVENANCE_TRACE_FIELDS` |
| `core/actions.py` | P19 | `declare` merges destinations for a second `chat.write` declarer (`:646-671, 791`) |
| `core/admission.py` | P17 | `active_run` (`:799`) |
| `modules/users/__init__.py` | P5 | Docstring only |
| `tests/test_actions.py` | P9, P19 | Tests of the two `core/actions.py` changes |
| `tests/test_stream_control.py` | P8 | Registry equalities rewritten to the three-entry value |

- Each change was a review-driven fix, names no platform, and is covered by
  tests.
- They still breach "the targets list is the complete set". The operator
  should ratify them as a spec target amendment.

### G5 (minor, escalated) — the proxy/agent_link spec declaration drops `model_proposable`; owner P1

- **Where:** the field lists are enumerated by hand in
  `modules/proxy/__init__.py:535-548` (`spec_declaration`, and its inverse
  at `:575-585`) and in `modules/agent_link/__init__.py:~480-495`.
  `model_proposable` is missing from all three.
- **Effect.** A proposable action lent across the proxy would come back
  `False`, and equality would exclude it. The P1 caller table said to flag
  exactly this.
- **Why it has no effect today:** `moderation.request` runs on the brain
  host, so the shipped topology does not trigger it.
- **Why it is escalated:** fixing it changes the protocol v1 `hello`
  declaration, which the spec freezes. It needs an operator decision
  (document the limit, or plan a protocol change).

### G6 (minor, escalated) — AC13 wording cannot hold for every key; owner P10, spec errata

- AC13 says the file name contains neither `v1` nor `c1`. A hex sha256 name
  can contain `c1`.
- The test passes for its fixed inputs (`tests/test_viewer_memory.py:212`),
  but the claim is not true in general.
- P10 flagged it. The spec wording needs an errata ("the name is the hash;
  the raw identifiers are not embedded").

### G7 (minor) — AC42 byte-identity is gate-verified only; owner P22

- No test compares the three phase 2 profiles with the base commit.
  `test_presence_pack::test_the_phase_2_profiles_are_not_the_presence_profile`
  checks only that they enable no phase 3 module.
- This gate verified 0 changed bytes by `git diff` (check 2).
- **Suggested fix:** pin a sha256 of each profile in a test.

## Requirement map (item 6)

| Req | ACs | Named tests (all passing), on the real module |
|-----|-----|-----------------------------------------------|
| R1 | AC1–AC7 | `test_brain::test_ac1_without_persona_or_routes_the_system_message_is_the_phase2_text`, `test_ac2_a_persona_replaces_only_the_identity_line`, `test_ac3_routes_select_the_instructions_and_the_delivery_list_per_run`, `test_ac4_*`; `test_twitch::test_notice_kinds_select_the_subscriptions`, `…_a_raid_is_one_raid_event_fed_and_admitted_for_the_raider`, `…_an_anonymous_community_gift_is_fed_and_never_admitted`, `…_a_rejected_follow_subscription_degrades_follow_and_chat_continues`; `test_presence_pack::test_ac6_the_presence_pack_runs_every_observation_row`; `test_profiles::test_main_check_config_exits_zero_on_the_presence_profile`; `test_hygiene::test_ac7_readme_presence_table_says_configuration_or_code`. **Gap G1:** no empty-text notice is run end to end. |
| R2 | AC8–AC12 | `test_clips::test_an_authorized_call_is_confirmed_after_exactly_one_create`, `…_a_call_29_s_after_the_create_is_refused_and_one_at_30_s_sends`, `…_lookup_empty_until_the_window_closes_is_clip_not_created`, `…_five_concurrent_calls_send_one_create_and_a_sixth_is_busy`, `…_ac12_with_twitch_and_kick_clips_are_ready_for_twitch_and_unsupported_on_kick` (twitch + kick) |
| R3 | AC13–AC19 | `test_viewer_memory::test_ac13_*` … `test_ac19_*` (files, eviction order and ties, total bound, retention, notes, corrupt files, erasure and CLI) |
| R4 | AC20–AC23 | `test_brain::test_ac20_a_delivered_reply_is_recalled_by_the_next_run`, `test_ac21_uncertain_failed_and_absent_deliveries_record_no_reply`, `test_ac22_memory_record_is_never_offered_over_a_granted_run`, `test_ac23_the_record_is_skipped_without_a_call_or_5_seconds_left`; `test_viewer_memory::test_ac22_*` |
| R5 | AC24–AC28 | `test_moderation::test_ac24_the_default_mode_alerts_with_no_platform_request_and_one_fact`, `test_ac25_the_real_module_is_offered_only_with_a_rule_and_once_per_run`, `test_ac26_*`, `test_ac27_delete_message_on_kick_is_platform_unsupported_with_no_request`, `test_ac28_the_twitch_service_deletes_once_and_is_applied`; `test_contracts::test_discovery_refuses_model_proposable_naming_module_and_action`, `test_only_moderation_request_is_model_proposable` |
| R6 | AC29–AC33 | `test_watch::test_ac29_*` … `test_ac33_shutdown_stops_the_ticks_within_the_global_deadline`; `test_brain::test_ac32_*`; the `:` identity refusals in `test_twitch`, `test_kick::test_ac32_*`, `test_youtube::test_ac32_*` |
| R7 | AC34–AC39 | `test_kick::test_ac34_*`, `test_ac35_*`; `test_youtube::test_ac36_*`, `test_ac37_*`, `test_ac38_*`, `test_ac39_three_platforms_on_one_runtime`, `test_ac39_core_names_no_platform`; `test_contracts::test_contracts_name_no_platform`. **Gap G2:** the poll reason. |
| R8 | AC40–AC42 | `test_profiles::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency`, `test_examples::test_manifests_are_unique_and_have_coherent_capabilities`, `test_hygiene::test_ac41_readme_records_the_phase_3_trials_and_versioning`, `test_hygiene::test_ac45_the_suite_makes_no_positive_duration_sleep_call`, `test_presence_pack::test_the_phase_2_profiles_are_not_the_presence_profile`. **Gap G7.** |

**Honesty checks on the tests:**
- No phase 3 `xfail` or `skipif`.
- Only two `pytest.skip` calls were added: the opt-in trials, and a tz-database
  cross-check in `test_youtube` (the rule itself is asserted without a skip).
- No assertion was weakened. Every rewritten allowlisted assertion
  (`test_examples`, `test_profiles`, `test_twitch`, `test_brain`,
  `test_stream_control`) now asserts the new exact value.

## Caller tables (item 9)

| Table | Rows verified |
|-------|---------------|
| `ActionSpec.model_proposable` | `core/contracts.py:1070, 1135-1144`; `core/loader.py:198, 1776`; brain `_offered_tools` (`:3710`); moderation manifest is the only `true`; `test_contracts` refusals. The proxy/agent_link row expected no change and asked for a flag if a field list is enumerated by hand. One is, so this is flagged as G5. |
| `event_kind` / `payload.kind` | `core/triggers.py` type, `_normalize`, `_evaluate_rule`, `_validate_builtin_rule`. `event_kind` is declared in the twitch, kick, youtube and watch manifests and in the fakeplatform manifest. chat_context and users are unchanged in code. |
| Brain principal and routes | `_offered_tools`, `_delivery_call`, `_deliver_all`, `_delivery_for`, `_system_prompt`, `validate_settings` are present. `persona` and `routes` are in `modules/brain/module.yaml:255-261`. Phase 2 profiles are unchanged. |
| Service kinds `clip` / `moderation` | twitch publishes both, kick and youtube moderation only, the fakeplatform its scripted services. Clips and moderation resolve per platform. The `test_stream_control` row was inaccurate (see check 9). |
| Catalog 11 → 17 | `test_examples` and `test_profiles` are at 17. `test_twitch::test_manifest_declares_twitch_source_and_sink` was rewritten. `test_main::test_pyproject_ships_core_and_modules_with_every_manifest` is unchanged and passing. |

## Deviations flagged by the steps (item 10)

All 25 step review loops ended APPROVE. P14 took 2 fix rounds, 16 steps took
1, and 8 took none. No reviewer finding was left open.

| Item | Status |
|------|--------|
| P14 `model_proposable` guard vs P3 `test_contracts` | Resolved: `test_contracts.py:605-616` `test_only_moderation_request_is_model_proposable` |
| P19 second `chat.write` declarer | Resolved (`core/actions.py:646-671`); scope escalated as G4 |
| P17 watch admission via the brain-owned scheduler | Resolved (`core/admission.py:799`, `modules/brain/__init__.py:326`); scope escalated as G4 |
| P9 trace fields in the executor | Resolved (`core/actions.py:202, 1803`); scope escalated as G4 |
| P8 poll-only registry equalities | Resolved in `tests/test_stream_control.py`; G4 |
| P5 twitch role attestation and the users docstring | Resolved; G4 (docstring) |
| Profile to fixture ripple | None in phase 3: P22 found `PROFILES` is an explicit dict, and no fixture broke |
| P21 poll `platform_unsupported` (AC39) | **Open:** G2 |
| P22 clips `runs` pseudo-platform | **Open:** G3 |
| P1 proxy/agent_link field list | **Open:** G5 |
| P10 AC13 wording | **Open:** G6 |
| P14 no platform publishes `companion_id` | Documented (`docs/README.md:699`). The companion's own messages are dropped at ingestion on all three platforms, so a moderation request against them ends `target_unknown`, never an effect. Accepted. |
| P21 youtube `parent_message_id` is accepted but not forwarded | Accepted limit (the API has no reply thread) |
| P23 clean-install test skipped (no pip in `.venv`) | Environment. Checked by hand at P23: 17 manifests. The static halves of AC40 run. |
| P24/P25 real platform trials not run (no credentials) | Recorded as "Non exécuté" in the README trial table (AC41 permits it); only the screen-watch trial is runnable here |
| P2 `minItems`/`uniqueItems` enforced in code; P6 constants local to the brain; P11/P12/P13 error-code and accounting choices | Accepted design choices with the same observable behaviour; no action |

## Not verified by this gate

- **Real platform behaviour.** EventSub, Kick webhook header names, the
  YouTube live-chat API and quotas are checked against scripted transports
  only; the P24 trials were not run.
- **The clean install** (`pip install` of the checkout), because the venv
  has no pip.
- **The refreshed YouTube access token** is not a declared credential, so
  redaction would not catch it. Its absence from traces rests on the code
  never emitting it, which `tests/test_youtube.py:881` checks for one path.

## Loop-back summary

| Finding | Owning step | Action |
|---------|-------------|--------|
| G1 | P5 (tests: P7, P18, P21, P22) | Fix step: accept empty-text notices in the brain; end-to-end tests per real platform and in the presence pack |
| G2 | P21 | Operator: add `modules/stream_control/__init__.py` to the targets, or amend AC39 |
| G3 | P9 (test: P22) | Fix step: clips derives platforms from platform service kinds only; tighten the presence-pack assertion |
| G4 | P5, P8, P9, P17, P19 | Operator: ratify the five out-of-target files as a spec target amendment |
| G5 | P1 | Operator: document the lending limit or plan a protocol change |
| G6 | P10 | Spec errata for AC13 |
| G7 | P22 | Optional: pin the phase 2 profile hashes in a test |
