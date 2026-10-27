# Self-hosting the companion with Docker

**English** · [Français](self-hosting.fr.md)

This guide is for someone who wants the companion on a server — a VPS, a NAS, a spare Linux machine —
and has not read this repository. It needs Docker Engine with the Compose v2 plugin on Linux, and
nothing else from the project: no checkout, no Python, no manual image build. The English text is
normative; the French translation follows it.

Where this guide and the Phase 10 specification (`docs/campaigns/phase10/spec.md`) differ, the
specification wins. Once the companion is running, the setup screens and the console are the ones
[`docs/first-run.md`](first-run.md) describes; this guide covers what is different in a container.

## 1. Quick start

Create an empty directory on the server, open a terminal in it, and type these three commands:

```sh
curl -fsSLO https://raw.githubusercontent.com/chpomob/twitch-ia-compagnon/main/compose.yaml
docker compose up -d
docker compose logs companion
```

1. The first command downloads the compose file, and only it, into the directory.
2. `docker compose up -d` starts the companion. **The first `up` builds the image**: no image is
   published in a registry, so Compose builds it from the repository's Git URL named in the compose
   file. That build needs `git` installed on the host (Compose clones the repository for it), and it
   only happens once: later `up` commands reuse the built image. A later phase may replace the build with a
   published image.
3. `docker compose logs companion` prints the container's log. Two lines give access, each labelled
   and each copied on its own: `Configuration UI: http://127.0.0.1:8765/` is the page's address and
   `Access token: …` is the token. If the log is still empty, the companion is still starting: run the
   command again. Each start prints a new pair; the last one is the valid one (see
   [The token](#6-the-token)).

The access URL is the address followed by `?token=` and the token:
`http://127.0.0.1:8765/?token=…`. Open it in a browser on the server itself, or — on a remote
server — through an SSH tunnel from your own computer (see [The token](#6-the-token)). On an empty
volume it opens the setup screens; pass them as [`docs/first-run.md`](first-run.md) describes, from screen 1 to the dry run of screen 5.

**If the repository is private**, the first command and the build need read access to it: the raw URL
then answers 404 to an anonymous `curl`, and the Git build fails. Obtain the compose file another way
(copy it from someone who has access) and give Docker credentials for the repository, or build from a
checkout as [The compose file line by line](#2-the-compose-file-line-by-line) describes.

## 2. The compose file line by line

The compose file declares one service, `companion`, and one named volume. Nothing in it is specific to
Docker Desktop, and no other compose file is needed.

| Line | What it does |
|---|---|
| `build: context: "${TWITCH_IA_COMPAGNON_BUILD_CONTEXT:-https://github.com/chpomob/twitch-ia-compagnon.git#main}"` | where the image is built from: the repository's `main` branch by default. Set `TWITCH_IA_COMPAGNON_BUILD_CONTEXT` to another context — for instance `.` from a checkout — to build from there instead |
| `image: twitch-ia-compagnon:local` | the local name of the built image; it is never pushed anywhere |
| `init: true` | a minimal init process as process 1, so that signals reach the companion and exited children are reaped |
| `restart: unless-stopped` | Docker restarts the container after a crash or a reboot, unless you stopped it yourself |
| `ports: - "127.0.0.1:8765:8765"` | the page is published on the **host's loopback only**: nothing outside the server reaches it. See [The token](#6-the-token) to expose it further |
| `volumes: - "companion-data:/data"` | every file the companion keeps is in this one volume, mounted at `/data` (see [The `/data` volume](#3-the-data-volume)) |
| `healthcheck:` | runs `twitch-ia-compagnon-config-ui --health` every 30 s (timeout 10 s, 3 retries, start period 90 s), the same as the image declares (see [Healthcheck states](#9-healthcheck-states)) |
| `# env_file:` / `# environment:` | commented out: how to give credentials to the container as environment variables (see [Environment variables and the credential file](#5-environment-variables-and-the-credential-file)) |
| `# secrets:` / `# command:` | commented out: a stable access token read from a file (see [The token](#6-the-token)) |
| `volumes: companion-data: {}` | declares the named volume; Compose prefixes it with the project name, which is the directory's name |

The image itself starts the configuration UI in container mode. Its default command is:

```sh
twitch-ia-compagnon-config-ui --host 0.0.0.0 --allow-non-loopback --no-browser --start-runtime
```

- `--host 0.0.0.0` binds every interface **inside the container**, so that the published port reaches
  the page. With `--allow-non-loopback`, which such a bind requires, the companion writes one warning to
  its log at each start (`config_ui: warning: bound to 0.0.0.0 port 8765: …`): the page is reachable
  from the network and protected only by the access token. In this compose file, "the network" is the
  host's loopback, through the published port.
- `--no-browser` never tries to open a browser: there is none in a container. The address and the
  token are still printed once, on two separate labelled lines (`Configuration UI:` and
  `Access token:`), so that one can be copied without the other.
- `--start-runtime` starts the companion once the page is listening, through the same **Start** as the
  console, when the setup is finished. With an unfinished setup, or no profile yet, it starts nothing
  and logs why, without any value.
- `--health` is the healthcheck: it prints one line and exits.

Two more options exist and are off by default: `--token-file` (see [The token](#6-the-token)) and
`--allowed-host` (one accepted host name for a wildcard bind). `--port` changes the listening port, and
`--config` another profile than the default one; the compose file uses neither.

## 3. The `/data` volume

The image sets `XDG_CONFIG_HOME=/data` and `HOME=/data`, so the companion's per-user directory is
`/data/twitch-ia-compagnon/`. The profile, the credential file and every record lie there, side by side.
Nothing else the companion writes lands elsewhere in the container, except temporary files.

| File | Role | Written by |
|---|---|---|
| `/data/twitch-ia-compagnon/profile.yaml` | the profile: the modules, their settings and `${NAME}` references to your credentials, never their values | screen 4 of the setup |
| `/data/twitch-ia-compagnon/profile.local.yaml` | the overlay: your changes from the editor, laid over the profile; absent until you use the editor | the editor |
| `/data/twitch-ia-compagnon/profile.yaml.status.json` | the status record: the running companion's process and each module's state, which the console and the healthcheck read | the companion while it runs |
| `/data/twitch-ia-compagnon/profile.yaml.feed.jsonl` | the feed: what just happened, as the console shows it | the companion while it runs |
| `/data/twitch-ia-compagnon/profile.yaml.wizard.json` | the setup progress: which screens are passed | the setup |
| `/data/twitch-ia-compagnon/profile.yaml.credentials.json` | the credential file: the values of your keys and tokens (mode `0600`) | screen 4 of the setup |
| `/data/twitch-ia-compagnon/profile.yaml.instance.json` | the instance record: the page's process, port, marker and access URL (so its token); present while the page runs | the page, at start; removed at a clean stop |
| `/data/twitch-ia-compagnon/profile.yaml.supervisor.json` | the supervision record: whether this page started the companion (`not_started`, `launched` with its process, or `stopped`), which the healthcheck reads | the page; removed at a clean stop |

The credential file and the instance record hold secrets: treat the whole volume like a password.

**Ownership.** `/data` belongs to the numeric user `10001` (group `10001`) with mode `0700`, and the
container runs as that user, never as root. A named volume, like the one in the compose file, inherits
that ownership by itself. If you replace it with a **bind mount** of a host directory (for instance
`./data:/data`), that directory must belong to uid `10001` and must not be writable by its group or by
others:

```sh
sudo chown 10001:10001 data && sudo chmod 700 data
```

Otherwise the companion cannot write there, or — for a directory that belongs to another user or that
others can write to — it refuses to store your credentials anywhere, says so, and screen 4 of the setup
stays closed, exactly as on a desktop.

## 4. First run and restore

- **An empty volume is a first run.** The access URL opens the setup screens. Once screen 5 is passed,
  the page shows the console; with `--start-runtime` the companion is started at the next start of the
  container — or right away with the console's **Start**.
- **A restored volume picks up where it was.** A container started over a volume a previous container
  used — stopped, killed or crashed — lands where that one would have: the console for a finished
  setup, the setup for an unfinished one. It keeps the stored credentials, and with `--start-runtime` it
  starts the companion again.
- **Records left behind block nothing.** A killed container leaves its instance and status records
  behind, naming process numbers that a process of the new container may hold again. A recorded
  process that started after its record was written is not the one that wrote it, so the record is
  treated as stale: the new page serves, and **Start** is not refused because of it.

## 5. Environment variables and the credential file

Your credentials reach the companion in one of two ways, never through the image:

- **through the setup screens**, which store them in the credential file of the volume,
  `profile.yaml.credentials.json`;
- **as environment variables given to the container**: uncomment `env_file:` (a file such as
  `companion.env` beside the compose file, one `NAME=value` per line, kept out of any repository) or
  `environment:` in the compose file. The companion inherits them.

The profile only ever holds references such as `${TWITCH_ACCESS_TOKEN}`; a value enters it through such
a reference and no other way.

**Precedence: a stored credential overrides an inherited variable of the same name.** When the console
starts the companion, a value stored in the credential file wins over an environment variable of the
same name given to the container. This is the precedence of every install, unchanged. So changing such
a variable in `env_file:` does not change a credential that the setup already stored.

No credential value is ever written to the container's log or to the health log.

## 6. The token

- **Printed once per start.** Without `--token-file`, the access token is generated at each start of
  the container, and printed once to the log on its own `Access token:` line. A restart makes a new
  one: read it again with `docker compose logs companion` — the last `Access token:` line is the
  valid one. Anyone who can read the container's log can read the token.
- **A stable token: `--token-file`.** With `--token-file PATH` the token is the content of that file,
  the same across restarts. It must be 43 to 128 characters from `A–Z a–z 0–9 - _`; surrounding
  whitespace is ignored. A missing, empty or invalid file stops the page at start (exit status 2), with
  a message naming the file and the rule broken, never its content. To use it, create the file beside
  the compose file, for instance:

  ```sh
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" > companion_token.txt
  ```

  The container reads that file as uid `10001`: give it to that user and keep it from others with
  `sudo chown 10001 companion_token.txt && sudo chmod 600 companion_token.txt`. Then uncomment the
  compose file's `secrets:` and `command:` entries and its top-level `secrets:`
  block: the file is mounted as `/run/secrets/companion_token`, and the command repeats the image's
  default one with `--token-file /run/secrets/companion_token` added. The token is never stored in the
  image.
- **From your own computer: an SSH tunnel.** The page is published on the server's loopback only. From
  your computer, `ssh -L 8765:127.0.0.1:8765 you@server` forwards it, and the access URL then works in
  your own browser as written above. Nothing in the compose file changes.
- **Exposing it beyond the host** is an explicit edit, never a default. Change the published address
  (for instance `"8765:8765"` for every interface of the server) and add one `--allowed-host NAME` to
  the command per host name your browser will use to reach it (a name or an address), then replace
  `127.0.0.1` in the printed URL with that name. Keep port `8765` on both sides: the page checks the
  name and the port each request carries. The page speaks plain HTTP, so the token and the session
  cross the network unencrypted: do this on a network you trust, and know that the token is then the
  only protection. No option disables the token.

## 7. The four profiles

The image carries the four ready-made profiles of the setup's screen 1, unchanged: `chat_qa`
(answers a chat message naming the companion), `channel_points` (reacts to one custom reward),
`spoken_keyword` (answers a spoken wake word) and `silent` (connected, never answers). The phase-10
trial renders each one with test values inside a container of the image, checks it with
`twitch-ia-compagnon --check-config` and runs its dry run, as the setup's screens 4 and 5 do.

`spoken_keyword` passes its check and its dry run in a container, but **a container has no
microphone**: it cannot hear you. The profile is shipped unchanged; in a container, prefer one of the
other three.

## 8. Backup, restore and upgrade

Everything to keep is the volume. Stop the companion first, so that no file changes while you copy it.
The commands below use the image itself, run as uid `10001`, so the restored files keep their owner:

```sh
docker compose stop
docker compose run --rm --no-deps -T --entrypoint tar companion -C /data -czf - . > companion-data.tgz
docker compose start
```

To restore that archive — on the same server or another one, in a directory holding the same compose
file — stop the companion, empty the volume and unpack the archive into it (Compose creates the volume
if needed), then start. The volume is emptied first because unpacking only overwrites the files the
archive holds: a file created after the backup, such as a newer `profile.local.yaml`, would otherwise
stay and be merged over the restored profile. On a new server the stop does nothing and the volume is
already empty:

```sh
docker compose stop
docker compose run --rm --no-deps -T --entrypoint sh companion -c 'find /data -mindepth 1 -delete && tar -C /data -xzf -' < companion-data.tgz
docker compose up -d
```

The archive holds your credentials: store it like a password. The restored container behaves as
[First run and restore](#4-first-run-and-restore) describes.

To **upgrade**, rebuild the image from the repository and start again; the volume is kept:

```sh
docker compose build --pull --no-cache
docker compose up -d
```

`--no-cache` makes Compose fetch the repository again instead of reusing the previous build. Never use
`docker compose down -v` unless you mean it: `-v` deletes the volume, so your profile and your
credentials.

## 9. Healthcheck states

The healthcheck, `twitch-ia-compagnon-config-ui --health`, reads what the companion writes in the
volume, asks the page `GET /instance` with the access token (the one request that opens no session),
and prints one line. Docker shows the result as `healthy` (exit 0) or `unhealthy` (exit 1, three times
in a row) in `docker compose ps`; the line itself is in the health log of
`docker inspect`. It never holds the token, a session or a credential.

There are five states: `ready`, `degraded`, `starting`, `idle` and `unhealthy`. The first line that
matches decides:

| Line printed | Meaning | Exit |
|---|---|---|
| `unhealthy: no instance record` | no page has published its record (not started, or stopped) | 1 |
| `unhealthy: not answering` | the page does not answer within 5 seconds | 1 |
| `unhealthy: token refused` | the page refuses the recorded token | 1 |
| `unhealthy: another instance` | something else answers on the port | 1 |
| `unhealthy: supervision record unusable` | the supervision record cannot be read | 1 |
| `unhealthy: supervision record not written` | the page could not record a start or a stop (its directory or volume refuses writes): neither idle nor running can be trusted; if not even this can be recorded, the page stops and the container exits | 1 |
| `idle: wizard unfinished` | the setup is not finished, so nothing is started | 0 |
| `idle: not started` | the setup is finished, the companion was not started since this page started | 0 |
| `idle: stopped` | you stopped the companion from the console | 0 |
| `unhealthy: runtime exited` | the companion this page started is no longer running | 1 |
| `ready` | the companion runs and every module is ready | 0 |
| `degraded: <names>` | the companion runs, these modules (sorted, comma-separated) are degraded | 0 |
| `starting` | the companion was started and has not reported yet, for at most 60 s | 0 |
| `unhealthy: runtime not ready` | started more than 60 s ago and still not reporting | 1 |

The start period (90 s) is longer than those 60 s, so a normal start is never reported unhealthy.

## 10. Image size

The image measures **147.7 MB** as reported by `docker image inspect --format '{{.Size}}'`,
measured in the phase-10 trial ([trial record](campaigns/phase10/trial-record.md)).

## 11. Limits

Container mode is the same product without a desktop around it. What that leaves out:

- **no desktop session**: the page is reached through a URL read from the log, not from a launcher's
  click;
- **no browser opener**: the companion never opens a browser (`--no-browser`); you open the URL;
- **no launcher**: `--install-launcher` writes a desktop launcher, which has no meaning in a container;
- **no microphone**: `spoken_keyword` cannot hear you there (see [The four profiles](#7-the-four-profiles));
- **no native bundle**: there is no installer and no packaged application; the image is built from the
  repository on your host and is not published in any registry;
- **no Twitch sign-in**: the setup's screen 2 asks for a client id, a client secret and an access
  token that it cannot make for you; the [first-run guide](first-run.md#4-the-five-setup-screens)
  says, in five steps, where each comes from.

The rest of the project's limits apply unchanged: it has never run on a real stream, and its
connections to Twitch, Kick and YouTube have never talked to the live services (see
[`docs/installation.md`](installation.md), section 13).
