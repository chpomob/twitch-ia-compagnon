# P12F1 — Make the container trial run, then fill the record from its measurements

P12 could not complete: the DEV produced an empty diff rather than invent a record. The operator then ran
the trial by hand. Here are the measurements, taken on this host, so nothing below is a guess.

## What was measured

Invocation (the one R7 documents):

    TWITCH_IA_COMPAGNON_CONTAINER_TRIAL=1 TWITCH_IA_COMPAGNON_BUILD_CONTEXT=. \
      .venv/bin/python -m pytest tests/test_container.py -q -p no:cacheprovider

Result: **4 failed, 192 passed in 495.74 s**.

- `test_trial_container_image` — `assert not ['GPG_KEY']`: the image's environment still carries
  `GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305`, inherited from `python:3.12-slim`.
- `test_trial_container_volume`, `test_trial_container_secrets`, `test_trial_container_health` — all three
  stop at the same place: `Failed: docker compose up did not report the service healthy: exit 1: Container
  ... Starting`.

The same image, started by hand, is healthy:

    TWITCH_IA_COMPAGNON_BUILD_CONTEXT=. docker compose -p tiac-diag up -d --build
    # -> Container tiac-diag-companion-1  Started
    # -> STATUS: Up 25 seconds (healthy)
    docker compose -p tiac-diag exec -T companion twitch-ia-compagnon-config-ui --health
    # -> "idle: wizard unfinished", exit 0
    # -> log: Configuration UI: http://127.0.0.1:8765/?token=...  (URL and token on separate labels)
    # -> log: config_ui: warning: bound to 0.0.0.0 port 8765: ... protected only by the access token
    # -> log: start-runtime: no profile

Image size, measured: **152 110 122 bytes** (`docker image inspect --format {{.Size}}`), shown by
`docker images` as `152MB`.

## What to fix

1. **The image must not carry its build environment.** The final stage must not expose `GPG_KEY`
   (nor any other variable that exists only to verify a build-time download). Remove it in the final
   stage — not in the build stage, which needs it — and keep `PATH`, `LANG`, `XDG_CONFIG_HOME`, `HOME`,
   `PYTHONUNBUFFERED`. The ordinary static check that asserts the image's declared environment must agree.

2. **The trial's health wait must match what the compose file declares.** Three nodes fail on a service
   that is merely `Starting`, while the same service becomes `healthy` in about 25 s and its own
   `start_period` is 90 s. A harness that gives up during `start_period` contradicts the declaration it is
   testing. Bring it in line: start with `up -d`, then poll `docker compose ps --format json` (or
   `docker inspect`) until the health status is `healthy` or `unhealthy`, with a deadline at least
   `start_period + interval × retries`, and fail with the container's last health log — never with a bare
   "did not report the service healthy". If the foreground `up` is kept anywhere, it must not be treated
   as a failure while the service is `Starting`.

Then re-run the five nodes with the invocation above and paste the summary line.

## What to write, once the trial is green

Only then: `docs/campaigns/phase10/trial-record.md` with every field the plan lists — who, host, Docker and
Compose versions, commit, the exact commands, timestamps, the measured image size in bytes, the healthcheck
states observed, the first run, the restart over the restored volume, the five nodes' outcomes, the outcome,
the limits — using the numbers **from your run**, never from this brief. Write the size in MB to one decimal
into both guides (`docs/self-hosting.md`, `docs/self-hosting.fr.md`) and add the ordinary checks AC13
(guide MB vs record bytes within 5 %) and AC25 (every field filled).

## Constraints

No new requirement, no renumbered criterion, no fabricated measurement. The ordinary suite stays green with
no daemon and still reports its five skips without the variable. One atomic commit per concern is allowed
(image fix, harness fix, record) — say which commit carries which.
