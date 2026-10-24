# Phase 9 — first-run trial record

Status: not run

The phase's criterion, in Amendment A1's words: from a working install to a first run in under
fifteen minutes, without a terminal and without YAML.

That claim rests on this record only. No test measures it: the suite checks that the record is
complete and consistent, never how fast a person was. It holds only when the elapsed minutes below
are under 15, and only for the platform this record names — a first run on one platform is never
claimed from an observation on another.

## How to fill it

A real non-technical person runs the trial on their own PC, following
[`docs/first-run.md`](../../first-run.md). The operator fills every field below after the trial and
sets the status to `run`. Only the phase gate sets `approved`, and only when the checker
`trial_record_problems` in `tests/test_first_run.py` reports no problem for this file.

- Every time is an ISO local time with its offset, to the second, in exactly this form:
  `2026-10-25T14:03:00+02:00`. Any other form is refused, not guessed.
- The install (getting Python and the package onto the machine and writing the launcher with
  `--install-launcher`) comes before the criterion and may be done by someone other than the
  streamer. Say who did it, by which means, and whether a terminal was used.
- Install duration and elapsed minutes are numbers of minutes (`35`, `11.5`).
- The trial starts at the first launch of the installed product (the click on the launcher) and
  stops at the first recorded run. "Terminal/YAML touched" covers that interval and is `no` or `yes`.
- Machine and platform names Linux, macOS or Windows, with its version.
- Limits lists what remains, the install step first: until Phase 10 the install is not
  terminal-free.

Never paste a token, a key, a password or the credential file's contents here.

## Record

- Date:
- Commit:
- Machine and platform:
- Install method (who, by which means, terminal used):
- Install duration (minutes):
- Profile:
- Trial start:
- Per-screen times:
  - Screen 1 passed:
  - Screen 2 passed:
  - Screen 3 passed:
  - Screen 4 passed:
  - Screen 5 passed:
- First-run time:
- Elapsed minutes:
- Terminal/YAML touched:
- Outcome:
- Limits:
