# P9F1 — Four defects in the container trial nodes

The review of P9 found one blocker and three majors. Fix exactly these four; do not redesign the nodes.

## 1 (blocker) — the AC7 filesystem scan discards its own failures

`tests/test_container.py:2543` reads the container's filesystem and does `except OSError: pass`. A stat
that fails is evidence that did not arrive; swallowing it makes an incomplete scan look complete, and it
is exactly the construct the project forbids. A failure must be recorded and reported as a finding of the
scan (naming the path and the error), never dropped.

## 2 (major) — the wizard and dry-run proofs escape the compose log

`tests/test_container.py:2816` runs the proof code through `compose exec`, whose output is not part of the
service's `docker compose logs`. The secrets trial therefore cannot see anything those proofs print — the
one place a credential would most plausibly leak (the wizard writes and reads configuration). Run those
proofs inside the service's own process stream (or capture their combined output AND assert on it
explicitly as part of the same trial), so a leak during the wizard or the dry run is caught by the trial
that claims to look for it.

## 3 (major) — the secrets trial reads the log only after a restart

`tests/test_container.py:3063` restarts the container before reading the health log, so the first start's
output is no longer the log being examined. Read what the container emitted during the **initial** start
and during the wizard before restarting, then read again after the restart, and require both: a leak at
first start must fail the trial.

## 4 (major) — the kill trial can observe the replacement

`tests/test_container.py:3105` kills the UI process and waits up to the health interval, but the compose
file ships `restart: unless-stopped`, so a healthy replacement can appear and the assertion can fail — or
pass — for the wrong reason. Make the trial unambiguous: either stop the restart policy for this trial
(the compose override, or `docker compose stop`/`kill` on the service rather than the process) or assert
on the specific exit event and the specific container instance that was killed, never on "some container
is healthy now". State which you chose.

## Constraints

Only these four; no new requirement, no renumbered criterion. The ordinary suite must stay green with no
daemon; the trial checks stay trial checks (Amendment A2 terms unchanged: one named class, enumerated,
asserted). No `except: pass`, no new `skipif` outside that class, no weakened assertion. One atomic commit.
Reply with the four diffs, the choice made in (4), and both suite counts.
