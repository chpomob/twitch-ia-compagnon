# P12F2 — The container is unhealthy under the trial, healthy by hand; and the record still does not exist

P12F1 fixed the harness's health wait, and that fix immediately paid: the trial now waits the way the compose
file declares, and it finds a defect the short deadline was hiding. P12F1 did not touch the image's
environment, and it wrote no trial record. Both remain.

## What the operator measured, after P12F1

    TWITCH_IA_COMPAGNON_CONTAINER_TRIAL=1 TWITCH_IA_COMPAGNON_BUILD_CONTEXT=. \
      .venv/bin/python -m pytest tests/test_container.py -q -p no:cacheprovider
    # -> 4 failed, 193 passed in 522.90 s

- `test_trial_container_image` — still `AssertionError`: the image's environment carries `GPG_KEY`
  (`GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305`, inherited from `python:3.12-slim`). Nothing in the
  Dockerfile unsets it.
- `test_trial_container_volume`, `test_trial_container_secrets`, `test_trial_container_health` — now fail
  honestly: `Failed: the service is 'unhealthy', not healthy (exit code 0); last health log:
  ['unhealthy: runtime exited', ...]`.

And the operator's control run, **same image, plain compose, empty volume**:

    TWITCH_IA_COMPAGNON_BUILD_CONTEXT=. docker compose -p tiac-diag up -d --build
    # -> Up 25 seconds (healthy)
    # -> log: start-runtime: no profile
    # -> twitch-ia-compagnon-config-ui --health  ->  "idle: wizard unfinished", exit 0

## What to do

1. **Name the difference.** The trial's project must differ from that control in something that makes the
   runtime start and exit. Find it — a command override, an environment variable, a mounted file, a volume
   seeded with a profile, a different service definition — and say in one sentence what it is. Do not widen
   the health contract to make the trial pass, and do not weaken the assertion: either the trial's setup is
   wrong (fix it) or the runtime's behaviour in that setup is wrong (fix the runtime, in the step that owns
   it, and say which).

2. **The image must not carry its build environment.** Unset `GPG_KEY` in the final stage (the build stage
   keeps it). Keep `PATH`, `LANG`, `XDG_CONFIG_HOME`, `HOME`, `PYTHONUNBUFFERED`. The ordinary static check
   that asserts the declared environment must agree with the Dockerfile.

3. **Then the record, from your own run.** `docs/campaigns/phase10/trial-record.md` with every field the plan
   lists — who, host, Docker and Compose versions, commit, the exact commands, timestamps, the measured image
   size in bytes (`docker image inspect --format '{{.Size}}'`), the health states observed, the first run, the
   restart over the restored volume, the five nodes' outcomes, the outcome, the limits. No number may come
   from this brief or from the operator's earlier runs: they were measured before your fix, and a record that
   describes a different image is a false record. Write the size in MB to one decimal into both guides
   (`docs/self-hosting.md`, `docs/self-hosting.fr.md`) and add the ordinary AC13 (guide MB vs record bytes
   within 5 %) and AC25 (every field filled) checks.

Acceptance: the five nodes pass with the invocation above (paste the summary line), the ordinary suite is
green with no daemon and still reports its five skips without the variable, and the record's size matches
`docker image inspect` on the image your run built.

Constraints: no new requirement, no renumbered criterion, no fabricated measurement, no widened health
contract. One atomic commit per concern, and say which commit carries which.
