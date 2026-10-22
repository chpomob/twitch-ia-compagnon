# Phase 8 — closing gate, fifth pass

## VERDICT: APPROVE

Reviewed HEAD `0c7468c3e73c5ddded0cf87930b2b74f3ae6f71e` against phase baseline `7782e5f62c7af7f614334ef5f5f053b93897885f`, applying the current specification's nine-entry allowlist plus Amendments A2 and A3, as this gate explicitly authorizes.

**F3 is RESOLVED: zero changed existing assertion sequences remain outside that allowance. F1 and F2 remain RESOLVED. Both full suites pass. Phase 8's closing gate is approved.** The fourth pass's scope objection is settled by the corrected allowance; no production change is needed.

Only this report was written in the checkout; nothing was staged. Review probes ran from standard input with bytecode disabled. Both suites disabled pytest's cache provider. No source, test, shipped profile or Git index was edited for a probe.

## F3: RESOLVED

### Assertion preservation: remainder 0

Recomputed from baseline and HEAD Git blobs: parse every Python file under `tests/`, key test definitions by file and qualified name, then compare ordered `ast.Assert` sequences using `ast.dump(..., include_attributes=False)`. These are definition counts, not parametrized outcomes.

| Measurement | Result |
|---|---:|
| Baseline / HEAD test definitions | 2,497 / 2,725 |
| Added definitions / removed baseline names | 228 / **0** |
| Existing functions with changed assertion sequences | **13**, across 9 files |
| Current original allowlist entries | **9** |
| Changed sequences covered by that list | **8** |
| Changed sequences also named by A2 | **1**, overlapping those eight |
| Changed sequences covered by A3 | **5** |
| Remainder outside allowlist + A2 + A3 | **0** |

The bilingual entry adds `triggers` to the module-level expected guide collection; its assertion sequence is unchanged. The installation node belongs to both the original list and A2, so it cannot be subtracted twice. Thus the unique coverage is **8 + 5 = 13**. The A3 introductory wording “0 of the three A2 nodes” does not describe that overlap literally; the measured overlap is one and does not affect the zero remainder.

### A3: all five expected-value adaptations justified; none loosened or deleted

Compared the complete assertion sequences for each named node against `7782e5f`:

| A3 node | Requirement-driven adaptation | Assert count |
|---|---|---:|
| `tests/test_audio_input.py::test_prepare_starts_no_task_and_spawns_nothing` | R7 adds the input lifecycle: `not hasattr(handle, "start_inputs")` becomes positive `hasattr`. Existing no-task/no-spawn and capture assertions remain; an added helper also exercises disabled listening through start/stop. | 7 → 7 |
| `tests/test_audio_input.py::test_the_loader_discovers_the_manifest_and_prepare_binds_the_declared_spec` | R7 changes exact roles from `frozenset()` to `frozenset({"input"})`. Binding, capture, credential and shutdown assertions remain. | 15 → 15 |
| `tests/test_contracts.py::test_platform_notice_kinds_exclude_message_and_watch_tick` | R4 appends `reward`, `manual`, `speech`, `timer`; the set subtraction excludes those too. The exact seven platform-notice names and their ordering remain asserted. | 5 → 5 |
| `tests/test_twitch.py::test_manifest_declares_twitch_source_and_sink` | R5 adds `reward` to the exact declared-type set and listable-kind sequence. The symbolic comparison changes `NOTICE_KINDS` to `LISTABLE_KINDS`, which is `NOTICE_KINDS + (REWARD_KIND,)`; the literal sequence independently pins all seven values. Default policy and other manifest assertions remain. | 35 → 35 |
| `tests/test_web.py::test_ac1_the_shipped_manifests_discover_19_modules_with_web` | R6/R8 ship `operator` and `timer`: exact count 19 → 21. All remaining assertions are unchanged. | 6 → 6 |

These are the requirement-driven constants/expected values A3 names. “A single constant” describes the semantic adaptation, not literally one `ast.Constant` edit per function: Twitch updates three assertion expressions, and the audio lifecycle check changes a negation. None changes an exact check into a permissive one or deletes an assertion.

Executed all five A3 nodes plus `test_ac33_the_whole_suite_runs_under_the_guard` and `test_ac33_no_test_reaches_a_real_spawn_or_socket`: **7 passed in 2.54s**.

### Kick: byte-identical

Executed `git diff 7782e5f HEAD -- tests/test_kick.py`: **empty output**. Both revisions have blob **`082faab70ed90b4b1ddf08b3f3354ab5648ead9b`**. The working tree also has no Kick modification.

### A2: exactly three real, named and journaled proofs

The current specification and `conftest.A2_REAL_NODES` name exactly:

- `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`
- `tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`
- `tests/test_kick.py::test_ac34_the_listener_runs_on_the_configured_host_and_port`

The 3.11 full-suite journal contains **4 installation process entries**, **7 AC24 Git process entries** and **4 Kick socket entries**, exclusively under those names. Installation builds from a pristine source copy, installs, checks discovery/import isolation outside the checkout, and executes installed launcher help. AC24 reads real Git repository facts. Kick binds the configured loopback listener. These are actual boundaries, not substituted successes.

The 3.10 journal contains the same **7 Git** and **4 socket** entries, plus **1 installation process entry** for the real pip-availability probe; its existing missing-pip skip is explained below.

Re-executed `test_a2_an_unlisted_node_is_refused_a_socket_and_a_process`: both `socket.socket(AF_INET, SOCK_STREAM)` and `subprocess.run(["true"])` raise the guard's node-specific `AssertionError`, and the journal stays unchanged. The exact-three-names test and real-entry-point/journaling self-check also pass. Together with the F1/F2 probes below, the targeted run returned **7 passed in 1.01s**.

No application socket outside A2's three names was observed in either full suite. The guard is autouse, unshadowed and active across collected tests; no operator trial was opted in. The documented asyncio infrastructure self-pipe exception remains. Other Kick nodes run through the in-memory transport supplied by conftest.

### A1: exclusion and failure sensitivity confirmed

AC24 still uses `SHIPPED_PROFILE_PATHSPEC = ("*.example", ":(exclude)tests/fixtures/**")`. Executed Git selection returns exactly `agent.yaml.example`, `config.server.yaml.example`, `config.yaml.example`, `presence.yaml.example`, with no fixture path. Its phase-7 comparison base remains correctly **`7c29434`**; the phase-8 preservation base is **`7782e5f`**.

The unchanged AC24 function passes with real Git. It asserts Git availability, work-tree existence, base-commit existence, an empty working-tree comparison and each shipped profile's presence at the base and on disk. Its diff has no HEAD endpoint, so it includes working-tree content.

Executed a sensitivity control without changing any checkout file: anonymous RAM descriptors held a private index and attributes; a Git clean filter appended a marker to the comparison content of `config.yaml.example`. The same private index without the filter passed. With the filter, actual Git returned exit 0 and:

```text
2       0       config.yaml.example
```

The same AC24 function then raised **`AssertionError` at `tests/test_web.py:6377`, `assert numstat.stdout.strip() == ""`**. Only the subprocess view was changed; Git output was not fabricated. Byte comparisons afterward confirmed all four shipped profiles and `.git/index` unchanged. This proves exclusion of fixtures and rejection of changed shipped-profile content while respecting the one-file write limit.

## F1, F2: still RESOLVED

**F1:** reran `test_p17f1_a_hook_that_never_yields_is_abandoned_at_the_hard_bound`. Its 0.01-second bound rejects a non-yielding 0.12-second hook under both default and `none_of` policies; heartbeat progress, the elapsed-time bound and retention of rejection after the late result all pass.

**F2:** reran `test_p17f1_the_listening_path_transcribes_only_an_accepted_candidate`, all three scenarios. It directly counts unfiltered transport requests: zero requests and factory calls after prepare, still zero requests over 600 seconds before start, then **0 / 0 / 1** transcription requests and source acquisitions over 60 seconds for never-firing / declined / accepted candidates. Candidate decisions observe **(source calls, transcription requests) = (0, 0)**. All pass.

## CI PARITY: GREEN

Both complete suites exited **0**, freshly executed in this pass.

Python **3.10.19**, supplied interpreter:

```sh
PYTHONDONTWRITEBYTECODE=1 /home/chpo/.hermes/cache/scratch/venv310/bin/python -B -m pytest tests/ -q -p no:cacheprovider
```

```text
4820 passed, 21 skipped in 555.03s (0:09:15)
```

Python **3.11.14**, project interpreter:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider
```

```text
4821 passed, 20 skipped in 407.67s (0:06:47)
```

The extra 3.10 skip is the baseline installation test's existing pip-unavailable branch. A separate focused `-rs` execution reproduces `No module named pip` at `tests/test_profiles.py:1147` (**1 skipped in 0.14s**). The 3.11 suite executes the full real installation proof. The other 20 skips are the default operator trials; no trial was opted in.

Baseline-to-HEAD AST comparison finds **0 added skip/skipif/xfail/importorskip calls**, **0 deleted Python test files**, and **0 removed baseline test-function names**. No new dependency: the entire pyproject diff only adds operator/timer package-data lines; runtime, optional and build requirements are unchanged. The four shipped profiles and `tests/test_triggers.py` also have empty baseline-to-HEAD diffs. Relative to the fourth-pass reviewed head `3c2c4b6`, only campaign documentation changed.

## NEW FINDINGS

**None from executed reproductions.** The corrected allowance closes the prior scope objection, and no remaining gate failure was reproduced.

## NOT VERIFIED

- Real installed-distribution behavior on the supplied 3.10 interpreter, which lacks pip; the equivalent proof passes on 3.11.
- A fresh hosted CI run or Python 3.12/3.13 execution.
- Live Twitch redemption, microphone/wake-word hardware, browser/hotkey/OBS operation, or an operator-filled phase-8 trial. `trial-record.md` still says `not run`.
- Real OS signal delivery and multi-process deployment behind converted fake-OS/in-process tests; A2 restores only its three specified real boundaries.
- A fresh repetition of the first review's entire AC1–AC40 mutation campaign. This pass reran both full suites and the specific preservation, A1/A2, F1 and F2 checks requested by the closing gate.
