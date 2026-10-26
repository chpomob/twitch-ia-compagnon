# Phase 10 — self-hosted instance trial record

This record holds one trial of the image and the compose file on a Linux host with Docker Engine and
the Compose v2 plugin (R8). Every figure below was measured in that trial, on the commit it names;
none is copied from an earlier run or from a brief. The ordinary suite checks that every field is
filled once (AC25) and that the guides' size in MB agrees with the bytes below within 5 % (AC13).

Never paste a token, a key, a password or the credential file's contents here.

## Record

- Who: the P12F2 development agent (Claude Opus 5.5), on the operator's machine, unattended
- Host: Ubuntu 24.04.4 LTS, Linux 7.0.0-34-generic x86_64, 16 CPUs
- Docker Engine version: 29.1.3 (client 29.1.3)
- Docker Compose version: 2.40.3+ds1-0ubuntu1~24.04.1
- Commit: `c52408d085bce32eade2ea968d43c459ec17859d`
- Commands:
  - the trial, from the checkout: `TWITCH_IA_COMPAGNON_CONTAINER_TRIAL=1 TWITCH_IA_COMPAGNON_BUILD_CONTEXT=. .venv/bin/python -m pytest tests/test_container.py -q -p no:cacheprovider`
  - the size, after it, on the image it built: `docker image inspect --format '{{.Size}}' twitch-ia-compagnon:local`
  - the walk-through, after it, on the same image: `docker compose -p tiac-walk -f compose.yaml -f compose.twitch.yaml up -d`, the wizard's screens 1–5 through the trial's wizard proof (`docker compose exec -T companion python -`), `docker kill`, `docker compose ... up -d`, then `down -v`; `compose.twitch.yaml` is the trial's fake-Twitch override (`_fake_twitch` in `tests/test_container.py`)
- Trial start: 2026-10-26T20:16:10Z
- Trial end: 2026-10-26T20:21:48Z
- Image size (bytes): 147656476
- Health states observed:
  - empty volume: `healthy`, health log `idle: wizard unfinished` (walk-through, 20:21:36Z)
  - after the wizard, before the restart: `healthy`, `idle: wizard unfinished` (the UI started no runtime)
  - restored volume, after `docker kill` and `up -d`: `healthy`, health log `ready` (walk-through, 20:21:48Z)
  - the supervised runtime SIGKILLed: `unhealthy`, last line `unhealthy: runtime exited`; the UI killed: exited, never `healthy` (asserted by `test_trial_container_health`, passed)
- First run: empty volume; the access URL redirected to `/wizard`; the `silent` profile's wizard passed screens 1, 2, 3, 4 and 5 (screen 5 the production dry run), wizard state completed (walk-through 20:21:36Z, and `test_trial_container_volume`)
- Restart over the restored volume: `docker kill`, then `up -d`: healthy in 11 s, the access URL redirected to `/console`, the credential file byte-identical to before, the runtime launched by `--start-runtime` reported `ready` (walk-through 20:21:37Z → 20:21:48Z, and `test_trial_container_volume`)
- Trial nodes: `203 passed in 301.03s (0:05:01)` — `test_trial_container_image` passed, `test_trial_container_volume` passed, `test_trial_container_secrets` passed, `test_trial_container_health` passed, `test_trial_container_profiles` passed
- Outcome: passed for the five trial nodes and the walk-through at this commit; the operator's own browser trial (AC15) was not performed in this step, see Limits
- Limits:
  - the platform was faked: the runtime nodes and the walk-through answer for Twitch's host names with the trial's own TLS service, because the trial's credentials are sentinels the real Twitch refuses (401, and the runtime then exits by design); no real channel was connected
  - the wizard was driven by the trial's proof code inside the container, not by a person in a browser; AC15's run from a directory holding only `compose.yaml`, with at most 3 typed commands and real credentials, remains the operator's to perform and record
  - the image was built from the local checkout (`TWITCH_IA_COMPAGNON_BUILD_CONTEXT=.`), not from the Git URL default, which builds `main` as pushed
  - one host, one architecture (x86_64); no microphone, so the spoken-keyword profile was proven by its check and dry run only (A7)
