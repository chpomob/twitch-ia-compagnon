# VERDICT: APPROVE

Reviewed 2026-10-25 at HEAD `72bb74c2b6f8b845044bf68cafc6c7d9b4899946`, against baseline `b7ec50d`, the second-pass findings, P19F3/P19F4 and spec Amendment A2. This report records fresh executions; prior suite results are not substituted for current results.

**N1, N2 and N3 are RESOLVED. F1 and F2 remain RESOLVED.** The automated closing gate is approved and closes phase 9. The operator record remains **`Status: not run`**; its absence does not block this gate. **Phase 9 remains not delivered until a real operator trial record exists and is accepted.**

## N1 — RESOLVED

Repeated the original reproduction using `test_first_run._Flow`, the same persisted wizard store and a temporary copy of the shipped tables. First persisted `language=fr`, removed only `wizard.screen2.title` from French, then called the real `StringTable.load`.

The original reproduction now stops at loading: `StringTableError("fr.json: 1 key missing")`. It cannot inject an accepted incomplete shipped table because loading refuses it. With that directory selected for a fresh startup over the existing profile, `main` exits **2**, calls neither serve nor opener, renders **0 English sentences**, and leaves the persisted language **fr**. Stderr is exactly:

```text
config_ui: string table refused: fr.json: 1 key missing
```

Also exercised the handler independently by injecting the formerly withdrawn-French table into a fresh UI over that same store:

```json
{"status":503,"body":"fr.json: 1 key missing","english_sentences":0,"persisted_language":"fr"}
```

POST `/language` requesting that unavailable French table returns **409**, body `409 Conflict: fr.json: 1 key missing`, **0 English sentences**, persisted language **fr**. The cause names the table and missing-key count, without exposing key names.

Controls, all freshly executed:

| Case | Measured result |
|---|---|
| Complete table; switch fr → en → fr | Each POST returns 303; each requested language persists and its screen-2 title renders. |
| Remove the same key from English | Loader refuses `en.json: 1 key missing`; startup exits 2, serves/opens nothing, renders 0 English sentences and preserves the existing fr store. |
| Inject withdrawn English into a persisted English session | GET screen 2 returns 503, body `en.json: 1 key missing`; 0 English sentences, language remains en. |

The focused run also executed all `test_p19f3_*` cases, including two missing French keys, placeholder mismatch, startup refusal, persisted-session refusal and refused switching.

## N2 — RESOLVED

Compared every baseline Python test function with current ASTs, matching by file and qualified function name, omitting source positions. Compared assertion lists and entire function ASTs; separately checked baseline assertion preservation in source order and inspected diffs, scan inputs and helpers.

Measured: **2,725 baseline test functions; 0 deleted; 8 changed assertion lists; 11 changed existing function ASTs.**

Every changed assertion carrier is named below. The counts are assertion sites, not parameter-expanded executions.

| Existing function | Baseline → current | Authority and disposition |
|---|---:|---|
| `tests/test_config_ui.py::test_a_missing_config_or_bad_port_exits_non_zero` | 3 → 5 | Original failure-allowlist entry, R1. No-argument startup is now valid; the replacement supplies a colliding default and checks refusal without serving. Both original bad-port assertions remain. |
| `tests/test_config_ui.py::test_ac35_no_secret_value_reaches_any_response_log_report_or_record` | 29 → 61 | Original route-set entry plus the P19F3 response-type clarification, R1/R7. Exact set gains eight routes; content-type constant gains JSON, pinned to exactly /instance and /feed. Added assertions drive the routes; existing secret, authority and response protections remain. |
| `tests/test_agentic_loop.py::test_ac31_the_core_and_the_neutral_modules_name_no_platform` | 2 → 5 | Named P19F3 allowlist entry, R2/R3/R5/A1. Exactly the DD1 constant set: locations.py, profiles/ and strings/ under core/config_ui/. Three added assertions pin the exemption and remaining scanned files; baseline assertion sequence retained. |
| `tests/test_youtube.py::test_the_broad_core_scan_names_no_platform_or_vendor` | 4 → 6 | Named P19F3 entry, same DD1 requirements and exact constant set; two assertions added, all four originals retained in order. The separate AC39 scan remains unchanged. |
| `tests/test_web.py::test_p3f1_one_huge_construct_still_reaches_the_deadline` | 3 → 5 | Named P19F3 entry, forced by P19F1. Original three assertions restored verbatim/in order, including the latency limits. Faithful deadline fake expires on the first read inside the walk; two assertions add observed-read and relative-work checks. |
| `tests/test_web.py::test_p12f4_the_search_for_a_tag_end_stops_soon_after_its_deadline` | 1 → 4 | Named P19F3 entry, P19F1. Original <0.05 s assertion restored; added whole-search result, exactly-one deadline read and relative-work assertions. Expiry is explicit, independent of CPU speed. |
| `tests/test_web.py::test_p12f4_the_json_walk_stops_soon_after_its_deadline_inside_one_value` | 1 → 2 | Named P19F3 entry, P19F1. Original <0.05 s assertion restored; one assertion adds exactly 100 deadline reads. Encoding happens before timing; the fake forces abandonment inside the scalar walk. |
| `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | 11 → 14 | Explicit AC26/R8 strengthening, which the spec labels separately from its failure allowlist. All 11 original assertions retained in order; only three assertion sites added for exact packaged filenames, existence and byte identity. The 21-manifest constant and isolation exemption remain unchanged. |

**Seven changed assertion carriers are in the named failure allowlist. One is outside that literal list: the explicitly named AC26 strengthening above. Unauthorized changes: 0.** That survivor changes no existing expectation or constant; it only adds checks forced by packaging the two string tables and four profiles. The DD1 survivors are exact requirement-forced constants, not an open-ended scan exclusion.

The other **three** changed existing functions have **identical baseline/current assertion sequences**, as the P19F3 spec explicitly requires:

- `tests/test_web.py::test_p3f1_the_deadline_bounds_extraction_on_the_real_clock`
- `tests/test_web.py::test_p12f4_a_6_mb_search_answer_times_out_at_1s_on_the_real_clock`
- `tests/test_web.py::test_p12f4_a_timed_out_scalar_answer_leaves_no_walk_running`

Their change is the faithful `hold_the_walk_past_its_deadline` fake, forcing a worker's actual deadline check to occur after expiry. No timeout expectation or latency ceiling is loosened.

Separately, `tests/test_docs_bilingual.py::GUIDES` gains the allowlisted `first-run` constant under R8. `test_the_discovered_pairs_are_exactly_the_declared_guides` itself has an unchanged AST and is not counted among the 11.

## N3 — RESOLVED under Amendment A2

Executed the branch diff, recursive Python AST/decorator scan, baseline comparison, collection and a focused run recording pytest call reports in memory.

Measured:

- **1** added `skipif` definition: the named `posix_only` exemption permitted by A2, with the explicit reason `A2: the subject is a POSIX-only facility (file modes, owners, flock)`.
- **8** enumerated uses: **6** whole-function decorators and **2** parameter marks; these collect as **10** runtime nodes because the writable-directory test has three modes.
- Added `skip`, `xfail`, `importorskip`, or `skipif` outside A2: **0**. The former inline POSIX `pytest.skip` is gone.
- The recursive invariant scanner finds **10 pre-phase skip/importorskip call sites**, exactly its baseline enumeration. An independent AST attribute comparison also finds only the one added authorized marker definition. Skip syntax inside mutation-test strings is not executable skip code.
- All **18 original alias-bearing functions executed: 27 parameter cases passed, 0 skipped** on this Linux host.
- Focused audit/regression run: **48 passed in 41.41s**, exit 0.

Invariant: `tests/test_first_run.py::test_a2_posix_only_is_the_one_exemption_and_its_uses_are_enumerated`. Its companion `test_a2_the_scan_sees_a_use_added_or_removed` passes mutation checks for added/removed/aliased uses and new skip, skipif, xfail, importorskip or imported skip syntax.

All names below are in `tests/test_first_run.py`. This accounts for every original alias-bearing function:

| Function | Current treatment |
|---|---|
| `test_ac11_the_credential_file_is_owner_only_under_the_per_user_directory` | A2: native directory/file modes and creation modes. |
| `test_ac11_a_group_or_other_writable_directory_is_refused` | A2: native directory modes; all three modes ran. |
| `test_a1_a_directory_owned_by_another_user_is_refused` | A2: native UID/ownership check. |
| `test_a1_concurrent_wizards_check_and_replace_one_at_a_time` | A2: native fcntl.flock. |
| `test_a7_the_state_file_is_owner_only_and_persists_only_screen_one` | A2: state file mode 0600. |
| `test_ac12_the_kept_credential_reaches_every_environment_redacted` | No skip; forces Windows credential-store branch. |
| `test_ac12_a_kept_credential_alone_counts_as_set` | No skip; forces Windows credential-store branch. |
| `test_ac31_a_confirmed_instance_of_this_version_is_opened_not_served` | No skip; linux_platform fixture forces branch. |
| `test_ac31_a_slow_first_attempt_then_a_confirming_one_within_ten_seconds` | No skip; linux_platform. |
| `test_ac31_the_recorded_port_wins_over_the_asked_one` | No skip; linux_platform. |
| `test_ac31_a_raising_opener_still_serves_nothing_and_prints_the_line` | No skip; linux_platform. |
| `test_ac31_an_unconfirmed_live_record_is_reported_and_kept` | No skip; linux_platform; four outcomes ran. |
| `test_ac31_a_stale_record_is_replaced_and_this_launch_serves` | No skip; linux_platform; four stale-record cases ran. |
| `test_ac31_the_published_record_is_owner_only_and_no_secret_leaves_it` | A2: native record mode 0600; also forces Linux branch. |
| `test_ac31_another_version_is_reported_and_never_opened` | No skip; linux_platform. |
| `test_ac31_two_launches_racing_serve_once_and_the_loser_opens_the_winner` | No skip; linux_platform. |
| `test_ac31_a_removal_holds_the_name_against_a_publication` | No skip; platform-independent pathname arbitration. |
| `test_ac11_no_response_carries_a_value_and_inputs_render_empty_and_kept` | Windows case runs without exemption; Linux parameter alone is A2 because it also asserts native mode 0600. Both ran. |

The eighth enumerated exemption use is `test_ac11_a_refused_directory_stores_nothing_and_keeps_next_closed[posix]`: its subject is native group-writable directory refusal. Its Windows parameter has no exemption and forces missing LOCALAPPDATA. Both ran. This replaces the earlier inline skip.

Additional platform-behavior carriers run without A2: `test_ac11_windows_stores_under_local_appdata`, `test_ac11_windows_without_local_appdata_is_refused`, `test_ac11_screen_four_names_the_windows_directory_and_why`, `test_ac31_windows_liveness_never_signals_the_recorded_pid`, and `test_ac27_install_launcher_writes_one_launcher_and_serves_nothing` (Linux/Windows/macOS launcher branches). Forced values establish branch behavior, not a native OS trial.

## F1 and F2 — still RESOLVED

Re-executed the gate crash reproductions using the real Supervisor, stderr drain and POST /start handler with an already-exited fake child. The wizard actually persisted the secret before starting; checked that it reached the UI's known-secret set. Captured the page, status record, log records and server's forwarded stderr, and decoded the closed raw-detail disclosure.

| Reproduction | Measured result |
|---|---|
| F1: 28-character canary in child's traceback | HTTP 200; whole-secret hits in page/status/server stderr/raw detail/logs = **0/0/0/0/0**. |
| F2: 9,042-character stored secret crossing read/retention bounds | HTTP 200; whole-secret hits on all five surfaces = **0**. Every suffix of length 12 through 9,042 is absent on every surface: **0 suffix hits**, including raw detail. No TAIL-FRAGMENT marker or 32-character filler run remains. |
| Operator negative control, no stored secret | HTTP 200; raw traceback and forwarded stderr match the expected original bytes exactly; ordinary failure text and exit status 1 remain visible. |

Both secret-bearing cases retain the traceback shape and `RuntimeError: [hidden]`, with exit-status 1 visible. Raw detail is **89 bytes** in each. The focused run also passed all `test_p19f2_*` cases, including split reads and overlapping matches.

## REGRESSIONS

No regression found in the executed reproductions, focused run or completed full suites.

Rechecked the slow-machine pattern across all **60** top-level test modules using text searches and AST-selected real-clock/timeout candidates, then inspected implicated fakes and helpers. **0 remaining cases found** where a required timeout/abandonment depends solely on a finite workload consuming a small wall-clock budget.

The six previously implicated web functions use observed/forced expiry or hold the worker until its deadline expires. Baseline latency assertions are restored. Unfinished-tag handling permits either valid fast completion or timeout. Other inspected cases use manual clocks, never-resolving futures, explicit timed waits or held writers/stores. The busy hook spins until a real clock deadline rather than for a fixed iteration count. Opt-in live trials permit observed outcomes and supply no slow-machine timeout assumption. Absolute latency ceilings can still fail under excessive load; this audit addresses dependence on a slow machine to obtain the expected timeout.

### Full suites

Project interpreter, **Python 3.11.14**, exit **0**:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

```text
5253 passed, 20 skipped in 509.95s (0:08:29)
```

Floor interpreter, **Python 3.10.19**, exit **0**:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/chpo/.hermes/cache/scratch/venv310/bin/python -m pytest tests/ -q -p no:cacheprovider
```

```text
5253 passed, 20 skipped in 692.13s (0:11:32)
```

Both full-suite commands disable bytecode and pytest's cache provider. They ran concurrently in separate processes. The normal sandbox failed before executing commands (`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`), so execution used the tool's reviewed escalation mode.

`tests/conftest.py` is byte-identical to baseline; no isolation exemption was added. Both full-suite AC33 journals contain exactly four socket constructions for the existing Kick listener node, four Popen calls for the installed-distribution node and seven for the baseline-profile node; no other node appears.

## NEW FINDINGS

**None from executed reproductions or audits.**

## NOT VERIFIED

- Real Twitch authentication/scopes/rewards/delivery and a real model endpoint; external edges were fakes.
- Native Windows/macOS launchers, ACLs or a real first run on those operating systems; forced platform values are branch checks.
- Python 3.13 or different/faster hardware. The required project and Python 3.10 interpreters were executed; the speed-dependence conclusion is confined to the inspected test pattern.
- The real non-technical operator trial. `docs/campaigns/phase9/trial-record.md` still says `Status: not run`. Acceptance of a real record, including installation method/duration under A1, remains required before claiming phase 9 delivered.

Only `docs/campaigns/phase9/full-branch-review-4.md` was created or modified for this review. Nothing was staged.
