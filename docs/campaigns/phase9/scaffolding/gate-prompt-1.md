# Phase 9 — full-branch closing gate

You are the closing reviewer of phase 9. The phase makes the product reachable by a non-technical
streamer: a first-run wizard, a console that answers « est-ce que ça marche ? », a launcher, four
ready-made profiles and a per-user credential store — all inside the existing local application.

**Write your report to `docs/campaigns/phase9/full-branch-review.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else.

## Authorities

- `docs/campaigns/phase9/brief.md` — the eight binding decisions and **Amendment A1** (the criterion starts
  after the install, and the record must show how the install was done and how long it took).
- `docs/campaigns/phase9/spec.md` — R1–R8, AC1–AC32, and the allowlist.
- `docs/campaigns/phase9/plan.md` — P1–P18 and this gate.
- `core/config_ui/__init__.py` as it stands, and the phase baseline `b7ec50d`.

## First job: the requirements and the criteria

For each of **R1–R8**, name the running tests that carry it and state PASS/FAIL/NOT-VERIFIABLE. Cover all
**32 acceptance criteria**, and say explicitly for each of the four profiles that it is `--check-config`
clean and that its dry run produced a **recorded run** with an id, an outcome and its origin — including
the silent profile, whose record must not pretend a trigger fired.

## Second job: try to defeat the surface, with executed evidence

- **No secret reaches a page, a log or a status record.** With a stored credential, poll every route, every
  status record and every log line for its value and for any substring long enough to matter; the rendered
  page must contain it zero times. Try the raw-detail disclosure too.
- **The token rules still hold.** A request without the token, with a wrong token, with a bad Host or
  Origin is refused; `/instance` requires the token like everything else and answers only the marker and
  the version.
- **The wizard cannot be advanced by a weak signal.** For each screen, make its observed success fail
  (Twitch refuses, the model errors, `--check-config` fails, the dry run produces no run) and show that the
  next screen is not reachable.
- **Regeneration and re-entry.** A profile the operator wrote is never overwritten; a profile this wizard
  wrote in the current pass may be regenerated; an unfinished wizard resumes after a restart and
  re-validates Twitch and the model; a completed wizard never reappears.
- **Second launch.** With a live instance, a second launch opens nothing new, starts no second server, and
  reports honestly; with a stale record, it is removed and a fresh instance starts; with a different
  version, it reports both and opens nothing.
- **The console cannot exceed the runtime.** Start and Stop act through the supervisor; Revoke writes the
  managed overlay; no console control can grant anything the runtime would refuse.
- **The bilingual table.** Every wizard and console sentence a French streamer reads comes from the table;
  a missing key is a failure, not an English fallback in a French session.

## Third job: the invariants of the suite

- No added `skip`, `xfail`, `importorskip` or deleted test; only the allowlisted expectations changed, each
  a constant or count the phase forces. Compare assertion ASTs.
- No new runtime dependency; no test opens a socket or spawns a process outside the named exemptions.
- **No test passes because the machine is slow.** A test that expects a timeout or an abandonment from a
  small wall-clock budget fails on a faster interpreter (this is how Python 3.13 went red on a
  documentation commit in phase 8). Check the suite for that pattern and report any instance you find.
- Full suite green on the project interpreter **and** on the floor interpreter:
  `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider` and
  `~/.hermes/cache/scratch/venv310/bin/python -m pytest tests/ -q -p no:cacheprovider`.

## Fourth job: what a non-technical streamer gets

- The guide pair (English normative, French translation) walks from an installed package to a first run,
  states the per-user directory per platform, and documents the launcher.
- The trial record template exists and its checker enforces the fields. **If the operator's record is
  absent, that does not block this gate**: approve what is built, and state plainly that the phase is
  **not delivered** until the record exists. What must never pass is a record claiming a trial that did
  not happen.

## Deliverable (write to `docs/campaigns/phase9/full-branch-review.md`)

- **VERDICT**: `APPROVE` / `REQUEST_CHANGES` / `REJECT`
- **REQUIREMENT MAP**: R1–R8 with named tests; the 32 criteria accounted for; the four profiles' dry runs
- **SURFACE ATTEMPTS**: one row per attack, with the observed result
- **TEST TREATMENT**: allowlist audit, socket check, both suite counts
- **NEW FINDINGS**: executed reproduction only, with severity
- **NOT VERIFIED**: plainly — no real Twitch, no real model endpoint, no real Windows trial

This gate CLOSES phase 9. If everything you can check passes, say APPROVE plainly.
