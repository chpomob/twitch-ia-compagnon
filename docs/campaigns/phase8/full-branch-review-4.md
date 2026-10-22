# Phase 8 — closing gate, fourth pass

## VERDICT: REQUEST_CHANGES

Reviewed HEAD `3c2c4b6af638851e42a9a6281917a9e4927c5628` (P17F4), including P17F3 `661ea0d`, against phase baseline `7782e5f` and second-pass head `d4f2504`.

**F1 and F2 remain RESOLVED. A1 and the A2 restoration pass. Both full suites are green. F3 is PARTIAL under this prompt's explicit eight-plus-A2 allowance: the measured outside count is five, not zero.** Committed Amendment A3 (`58145e4`, spec section “Amendment A3”) names and explains all five. Including A3 makes the outside count zero. This is a disagreement between the requested gate scope and the amended specification, rather than a reproduced loss of coverage. I requested clarification while completing the independent checks; no answer arrived. I have applied the explicit scope in this review request rather than silently enlarging it.

The remaining closing action is to reconcile the gate allowance with A3. No production fix is indicated by the executed checks. An approval under A3 would be supportable, but the narrower numerical condition requested here did not pass.

Only this report was written in the repository; nothing was staged. Probes ran from standard input with bytecode disabled. No tracked profile, source, test, Git index or commit was changed.

## F3: PARTIAL

### Assertion preservation

I parsed every baseline and HEAD Python file under `tests/`, keyed test functions by file and qualified name, and compared their ordered `ast.Assert` dumps with source attributes excluded. Counts refer to definitions, not parametrized outcomes.

| Measurement | Result |
|---|---:|
| Baseline test definitions | 2,497 |
| HEAD test definitions | 2,725 |
| Added definitions | 228 |
| Removed baseline names | **0** |
| Existing functions with changed assertion sequences | **13**, in **9 files** |
| Changed functions in the original allowlist | **8** |
| Changed functions named by A2 | **1**, overlapping the original eight: the installation test |
| Changed functions outside original allowance plus A2 | **5** |
| Those five explicitly named by A3 | **5** |
| Changed functions outside original allowance plus A2 plus A3 | **0** |

The original allowlist has nine entries; eight have changed assertion sequences. The bilingual-guide entry changes its expected set outside the function's assertions. The installation node belongs to both the original list and A2, so it must not be counted twice.

The five remaining nodes under the prompt's scope are exactly:

- `tests/test_audio_input.py::test_prepare_starts_no_task_and_spawns_nothing`: `not hasattr(start_inputs)` becomes `hasattr(start_inputs)`; an additional helper exercises disabled listening.
- `tests/test_audio_input.py::test_the_loader_discovers_the_manifest_and_prepare_binds_the_declared_spec`: roles become `{"input"}`.
- `tests/test_contracts.py::test_platform_notice_kinds_exclude_message_and_watch_tick`: excludes the four appended kinds too.
- `tests/test_twitch.py::test_manifest_declares_twitch_source_and_sink`: updated listable kinds and declared reward type.
- `tests/test_web.py::test_ac1_the_shipped_manifests_discover_19_modules_with_web`: module count becomes 21.

These changes match A3's named requirement updates; no unexplained assertion change remains. Nevertheless, subtracting only the original eight and A2, as requested, leaves five. Reporting zero for that narrower comparison would be inaccurate.

The original `test_install_test_skips_only_when_pip_is_unavailable` name is restored. There is no missing baseline function requiring a replacement explanation. The real installation test again asserts installed import isolation, the installed modules directory, manifest discovery, launcher existence and successful launcher help. Its assertion count remains **11 → 11**.

### Kick preservation

Executed:

```sh
git diff --numstat 7782e5f HEAD -- tests/test_kick.py
```

**Empty output.** Both revisions identify the same blob, `082faab70ed90b4b1ddf08b3f3354ab5648ead9b`: byte-identical.

### A1: RESOLVED

AC24 uses `SHIPPED_PROFILE_PATHSPEC = ("*.example", ":(exclude)tests/fixtures/**")`. Executing Git's selection yields exactly `agent.yaml.example`, `config.server.yaml.example`, `config.yaml.example` and `presence.yaml.example`. Fixture copies also carry `.baseline` suffixes.

The original Git proof is real again: Git availability, work-tree existence, base-commit existence, an empty work-tree diff and each shipped file's presence at the base and on disk are asserted. This older phase-7 AC24 correctly retains its own base **`7c29434`**; the whole-phase-8 preservation comparison uses **`7782e5f`**. Its diff is `git diff --numstat 7c29434 -- '*.example' ':(exclude)tests/fixtures/**'`, with no HEAD endpoint, so it includes working-tree changes.

Executed the unchanged test successfully. For sensitivity, anonymous RAM-only index and attributes descriptors forced Git to read the profiles, and a Git clean filter appended one marker line to the in-memory comparison content of `config.yaml.example`. The control with the same RAM index and no filter passed. With the filter, the real Git subprocess returned:

```text
1       0       config.yaml.example
```

The same AC24 function then raised `AssertionError` at its empty-diff assertion. Git output was not fabricated; no profile or filesystem temporary file was written. This establishes sensitivity to modified shipped-profile content while preserving the checkout.

### A2: RESOLVED

`conftest.A2_REAL_NODES` contains exactly these three real nodes, also named in spec Amendment A2:

- `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`
- `tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`
- `tests/test_kick.py::test_ac34_the_listener_runs_on_the_configured_host_and_port`

The Python 3.11 full-suite journal records **4 Kick socket entries**, **4 installation process entries** and **7 AC24 Git process entries**, all under those names. The installation uses a pristine source copy, pip installation, a child discovery interpreter outside the checkout and the installed launcher. Kick binds its configured loopback listener. AC24 invokes real Git.

Explicitly reran `test_a2_an_unlisted_node_is_refused_a_socket_and_a_process`: both `socket.socket(...)` and `subprocess.run(["true"])` raised the guard's node-specific `AssertionError`; the journal stayed unchanged. The exact-list and real-entry-point/journaling self-checks also passed. Together with F1/F2 below, this targeted command returned **`7 passed in 0.84s`**.

## CI PARITY: GREEN, WITH ONE ENVIRONMENT SKIP EXPLAINED

Executed both complete suites, exit **0** each. Bytecode was disabled for both, and pytest's cache provider was disabled.

Python **3.10.19**:

```sh
/home/chpo/.hermes/cache/scratch/venv310/bin/python -m pytest tests/ -q -p no:cacheprovider
```

```text
4820 passed, 21 skipped in 555.81s (0:09:15)
```

Project Python **3.11.14**:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider
```

```text
4821 passed, 20 skipped in 407.08s (0:06:47)
```

The one-outcome difference is the installation test's baseline pip-availability skip. The supplied 3.10 interpreter has **no pip module**. Independently reproduced with `python -m pip --version` and a focused `pytest -rs` run, which reports `No module named pip` at `tests/test_profiles.py:1147`. Its journal records the real pip probe; installation is then skipped. Python 3.11 executed the complete real installation proof. I did not modify the supplied environment.

A textual scan of all **86 Python files** under `tests/` and `modules/` found **0 matches** for `asyncio.timeout`, `asyncio.TaskGroup` or `asyncio.Runner`.

The immediate-stop fake-instance test now schedules `loop.call_later(FAKE_WAIT_SECONDS, current_task.cancel)`, awaits `fake.stop()` in that same task, fails if cancellation reaches the deadline, and cancels the timer in `finally`. It retains the stop-before-handler-start race without using a 3.11 API or moving stop into a separate task. It passed in the 3.10 full suite.

Presence waits for the observable it asserts: `Presence.drive` waits until exactly one additional `TRACE_BRAIN_RUN_COMPLETED` record exists, then returns that terminal record. Its predicate wait has a 20,000-turn budget with a 30-second wall-time floor; it yields rather than counting a fixed number of sleeps before inspecting the result. P17F4 additionally binds runtime deadlines and the executor sleeper to the same `ManualClock` already used by the modules, and explicitly asserts those bindings. Worker-thread scheduling therefore cannot consume a real-time executor/run budget while the test clock is stationary. The fix does not enlarge the existing wait parameters.

Executed the AC6 observation-row test **20 separate times on Python 3.10**, with a parallel `while True: pass` CPU process running throughout: **20/20 passed, zero failures**. Each repetition used bytecode/cache suppression and returned exit 0; passing durations ranged from **0.62 to 3.68 seconds**.

## F1: still RESOLVED

Reran `test_p17f1_a_hook_that_never_yields_is_abandoned_at_the_hard_bound`, covering default and `none_of` policies, heartbeat progress and rejection retention after the hook ends. It passed.

Also executed an independent real-clock `none_of` probe: bound **0.01 s**, non-yielding hook spins **0.12 s** then returns true. Observed **`accepted=False`, `rejected:wake_hook_timeout` in 0.020624 s**. The 20 ms heartbeat fired at **0.020636 s**, while the hook was unfinished. The late result did not replace the recorded rejection. This remains well below the busy-hook duration, including scheduling overhead under concurrent suite load.

## F2: still RESOLVED

Reran all three parametrizations of `test_p17f1_the_listening_path_transcribes_only_an_accepted_candidate`: never fires, declined, accepted. All passed.

The probe directly inspects unfiltered transport requests: **0 requests and 0 transport-factory calls after prepare**, still **0 requests after 600 seconds before start_inputs**, then **0 / 0 / 1 requests** respectively over 60 seconds of listening. Candidate scenarios observe **`(source calls, transcription requests) == (0, 0)` at decision time**. Source acquisitions are likewise **0 / 0 / 1**. The original AC26/AC37 carriers also ran green in both full suites.

## REGRESSIONS

**No functional regression reproduced.** There is no `core/` or `modules/` diff since the second-pass head. P17F4 changes only the two named test files plus campaign bookkeeping; it changes no public behavior.

R1–R9 and **all 40 functional criteria retain the second-pass PASS assessment**, with the same carriers documented in [the second pass](full-branch-review-2.md). AC33's scope now explicitly includes A2's three journaled real proofs; the previous loss of installation/Git proof is repaired. The complete suites reran the carriers; this pass did not repeat the first review's entire AC1–AC40 mutation campaign. F3 remains a separate preservation gate.

- Relative to `7782e5f`: **0 added skip/skipif/xfail/importorskip call ASTs**, **0 deleted test files**, **0 removed test-function names**. The 3.10 extra runtime skip invokes the pre-existing pip-unavailable branch.
- Relative to the second pass, restoration brings back four baseline skip calls and the original installation-check name. Restoring Kick's exact bytes removes the two phase-added `test_ac33_*` helper checks; no baseline Kick test is removed. These are restoration deltas, not a claim that the intermediate suite was untouched.
- **No new dependency.** The whole-phase pyproject diff only adds operator/timer package-data entries. Runtime requirements remain `aiohttp>=3.9,<4` and `PyYAML>=6,<7`; optional/build requirements are unchanged.
- The four shipped profiles and `tests/test_triggers.py` have **empty diffs against `7782e5f`**.
- No unlisted application socket/process exception was added or observed. The default suites remain guarded, opt-in trials remain unselected, and the explicit socket/process negative control passes. Asyncio's existing infrastructure wakeup socketpair remains the documented self-pipe exception. Other Kick nodes use the in-memory transport installed from conftest; only the named listener node opens a real socket.

## NEW FINDINGS

**No new behavioral finding from executed reproductions.** The remaining F3 scope discrepancy is the measured five-node remainder documented above, already explained by committed A3; it is the sole reason for REQUEST_CHANGES under the prompt's narrower allowance.

## NOT VERIFIED

- Real installed-distribution behavior on the supplied Python 3.10 environment: its existing pip-unavailable skip was taken. The same proof passed on 3.11.
- Python 3.12/3.13 and a fresh GitHub CI execution were not run here.
- No live Twitch redemption, microphone, real wake-word detector, browser/hotkey/OBS trial or operator-filled phase-8 trial record. The record still says `not run`.
- Converted fake-OS and in-process checks still do not establish real OS signal delivery or a real multi-process deployment. The restored A2 installation, Git and Kick proofs establish their specific real boundaries only.
- No fresh authorization to widen this prompt's eight-plus-A2 scope to include A3 was received. With that amended allowance, the measured assertion remainder is zero and no executed functional check supplies another blocking finding.
