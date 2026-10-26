# Brief — Phase 10: distributing it (the image, the profiles it carries, and the self-hosting guide)

Project: `twitch-ia-compagnon` (repo `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`,
HEAD `5c64399`). Phases 0–9 are approved; 5,253 tests green; CI green on Python 3.10–3.13.

## Problem

Phase 9 made the product reachable for a streamer on his own machine. It did nothing for the other half of
the product's deployment decision — « le PC du streamer **ou** un serveur distant » — and it left the four
ready-made profiles reachable only from a wizard that runs on a desktop session. Someone who wants the
companion on a VPS or a NAS has to read the repository, build an environment and discover the flags.

There is also a promise the repository cannot keep today: the guide says the product can run headless, but
nothing ships that makes a container work — the UI binds to loopback, the token is generated per run, and
the state directory has no documented volume.

## Binding decisions (operator, 2026-10-25)

- **1 — The audience is the self-hoster, and the criterion is measured in commands.** A technical person
  with Docker and a compose file gets a first run **with no repository checkout and no manual build**:
  `docker compose up`, the URL and the token, the four profiles available, a first run recorded. This is
  not the non-technical streamer's path — that one is a native bundle, which is a later phase.

- **2 — One image, built from what the repository already produces.** The image installs the project's own
  distribution (wheel/sdist), pins its base image, runs as a **non-root** user, and adds no runtime
  dependency beyond the two the project declares. Its size is measured and documented, not guessed.

- **3 — Secrets are supplied, never baked.** Credentials and model keys come from environment variables or
  a mounted file; the image contains no secret, no default key and no baked profile. The existing
  `${...}` reference mechanism stays the only way a value enters a configuration.

- **4 — The container exposes the UI safely or not at all.** Binding to a non-loopback address is an
  explicit setting with an explicit warning, the access token is **required** in that mode (no
  token-less network exposure), and the token is printed once to the container log at start — never the
  credential values, which stay under the phase-9 redaction rules.

- **5 — State lives in one volume, and the person can find it.** Profile, overlay, credential file, wizard
  state, instance record and feed live under one documented directory mounted as a volume; the guide names
  every file and what happens when the volume is empty (first run) and when it is restored (no surprise).

- **6 — The healthcheck tells the truth.** It reports ready only when the runtime is actually serving and
  its supervisor is alive — not merely when the process started. A container that is degrding (`degraded`
  modules) stays healthy but says so; a container whose process died is unhealthy.

- **7 — The four profiles travel with the image and are proven inside it.** Each profile is present in the
  image, passes `--check-config` **inside the container**, and its dry run produces a recorded run there —
  not only on the build machine.

- **8 — The guide is written for someone who has not read the repository.** A self-hosting guide (English
  normative, French translation) covers: the compose file, the volume, the environment variables, the token,
  the four profiles, backup and upgrade, and the honest limits of container mode (no desktop session, no
  browser opener, no launcher).

## What this phase must not do

- Ship a native Windows/macOS bundle or an installer: that is a separate phase, because a bundle cannot be
  proven on a Linux CI runner and must be verified by an operator trial before it is promised.
- Change a product behaviour to make containerisation easier. If a requirement needs a new setting, it is
  a setting, not a rewrite of how the runtime works.
- Add a runtime dependency, a second image, or a compose file that assumes Docker Desktop.
- Publish anything: no registry push, no published tag, no release, no GitHub release. A local tag
  (`twitch-ia-compagnon:local`) and local branch refs are normal and are what the compose file uses;
  building and running locally is the whole phase.

## Shape of the work

First the container contract (the settings that make headless mode safe, the volume layout, the token in
log mode), then the image and the compose file, then the profiles proven inside it, then the healthcheck,
then the guide pair and the trial record for a self-hosted instance, then the gate.
