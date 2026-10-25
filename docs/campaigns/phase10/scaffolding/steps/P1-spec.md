# P1 — Stale-record rule — a live pid that started after the record is not its publisher (R2, AC10)

Plan: `docs/campaigns/phase10/plan.md`, step P1. Specification:
`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.

## The plan's own words

- **Files:** [core/config_ui/__init__.py, tests/test_container.py]
- **Description:** Add a process start-time reader `process_started_at(pid) -> float | None`
  (wall-clock UTC epoch seconds): on Linux read field 22 (`starttime`, clock ticks
  since boot) of `/proc/<pid>/stat` divided by `os.sysconf("SC_CLK_TCK")`, added to
  a boot time taken as `time.time() - <first field of /proc/uptime>` (both read in
  one call; centisecond precision — not `btime` of `/proc/stat`, which is whole
  seconds and would shift a start up to 1 s earlier, letting a pid reused within
  that second pass as the publisher); on any other platform, or on any
  `OSError`/parse failure, return `None`. Add a judgement helper `record_pid_live(pid, published_at,
  liveness, started_at)` that returns `liveness(pid)` unchanged when the pid is not
  live or `started_at(pid)` is `None`, and returns `False` whenever the live process
  started strictly after `published_at` — `started_at(pid) > published_at`, with
  **no tolerance or grace interval** (finding P1: R2 and AC10 admit no exception, so
  a pid reused 0.5 s or 1 ms after publication is judged not live). Granularity
  cannot wrongly stale a genuine publisher: a publisher necessarily started before
  it wrote its record, and tick truncation of `starttime` only moves its computed
  start earlier, never later.
  Wire it into the two judgements R2 names: (a) the instance-record stale judgement
  in the claim path (publication time = the record file's `st_mtime`, the record
  format unchanged); (b) the status-record "foreign live pid" refusal used by
  Start (publication time = the status record's own timestamp field, read by the
  existing reader, falling back to `st_mtime`). Add an injectable
  `started_at: Callable[[int], float | None]` to `InstanceSeams` (default looked up at
  call time, like the other seams) and the equivalent seam on the Start-refusal
  path, so existing tests that inject `liveness` keep their results (their pids are
  judged with `started_at` defaulting to the real reader, which for a fake pid
  yields `None` or a time before the record → unchanged; where that is not
  guaranteed, the default seam is applied only when liveness is the default, see
  Risks). Create `tests/test_container.py` with a module docstring describing the
  ordinary/trial split of R7 and the first ordinary checks.
- **Dependencies:** []
- **Tests:** In `tests/test_container.py` (socket-free): AC10 — instance record and
  status record whose pid is live (fake liveness) with `started_at` later than
  publication → instance record removed as stale and `main([], serve=<fake>)`
  serves once; Start is not refused by the status record. Same records with
  `started_at` earlier → judged as today (refused/opened as in phase 9). With
  `started_at` returning `None` → unchanged judgement. Boundary cases of the
  "strictly after, no tolerance" rule (finding P1), each on both the instance-record
  path (record `st_mtime` set with `os.utime`) and the status-record path:
  `started_at = published_at + 0.5` → not live; `published_at + 0.001` → not live;
  `published_at + 1.5` → not live; `started_at == published_at` → live (judged as
  today); `published_at - 0.001` → live. A static check asserts the helper module
  defines no tolerance/grace constant applied to the comparison (the comparison is
  `>` on the raw values). Unit test of the `/proc`
  parser against fixture strings (including a `comm` containing spaces and `)`),
  read through an injected reader, no real `/proc` read; the reader computes boot
  time from injected `/proc/uptime` text and an injected clock (fractional seconds
  kept, e.g. uptime `"1234.56 99.0"`, clock `10000.25`, starttime `123400` ticks at
  100 Hz → `10000.25 - 1234.56 + 1234.00`), proving sub-second precision. Run
  `tests/test_first_run.py` and `tests/test_status_record.py` unchanged (existing
  instance-record and foreign-pid tests pass, AC10's second sentence).
- **Risks:** `/proc/<pid>/stat` `comm` field may contain spaces/parentheses — parse
  from the last `)`. Clock skew between `btime` and `st_mtime` (NTP step, container
  started after a host clock jump) could mark a genuine publisher stale; no tolerance
  is added for it (finding P1 — a tolerance would let a pid reused within the window
  pass as live, which R2/AC10 forbid); a backwards clock step between publication and
  judgement is out of scope of R2 and is noted in the helper's docstring. Computing
  boot time from `/proc/uptime` and the wall clock at read time follows the current
  wall clock, consistent with `st_mtime` and the status record's timestamp. Existing tests that fake liveness for
  the test process's own pid (a real process started before the fixture record was
  written) must stay unaffected — verify by running them; if any injected-liveness
  test changes outcome, make the default `started_at` apply only when `liveness`
  is the default, and record that decision in the docstring (non-trivial: branching
  logic + invariant the type system cannot verify).

## Standing phase-10 decisions that override any contrary reading of a requirement:

- **No new runtime dependency and no second image.** The image installs the project's own
  distribution; whatever the container needs beyond it is a build-time tool, not a runtime one.
- **Secrets are supplied, never baked.** No image layer, environment variable, label or default
  configuration may carry a credential, a key, a token or one of the operator's profiles. The
  `${...}` reference mechanism stays the only way a value enters a configuration. A hidden file
  (`.env`, `*.instance.json`, `*.supervisor.json`, anything under the state directory) must never
  reach the build context.
- **The ordinary suite stays green with no Docker daemon.** Checks that read files (the Dockerfile's
  declared properties, the compose file's shape, `.dockerignore`'s coverage) are ordinary tests.
  Anything that needs a daemon is a trial check: it runs where a daemon exists (this machine, the
  `container-trial` CI job) and the ordinary suite neither skips silently nor pretends it ran. A new
  exemption follows phase 9's Amendment A2 terms: one named class, enumerated, asserted by an
  invariant test.
- **`/instance` stays session-free** (token, no session, marker and version only), exactly as phase 9
  defined it; every other route requires a session. The container's healthcheck uses the probe.
- **One volume for all state**, documented file by file, and a restored volume must not change
  behaviour silently.
- **A state the health command cannot compute from the records it reads is not one of its states.**
  Remove it or make it observable — never guess.
- **Nothing is published**: no registry push, no tag, no release, no GitHub release. Building,
  running and measuring locally is the whole phase.
- Never weaken an existing test to make a step pass. If an existing test must change, the plan names
  it, the change is an added expectation (an allowlist entry), and silently dropping an assertion is
  a rejection.

Loop constraints: run `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` before committing;
one atomic commit per step; no `git push`; no edit to `docs/campaigns/phase10/{spec,plan}.md`.
