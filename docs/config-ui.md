# Configuration UI — operator guide

The configuration UI is a small web page to read and edit the configuration of
`twitch-ia-compagnon`, check it, and restart the main process on it. It runs in
**its own process**, separate from the main process (`core.main`), and adds no
runtime dependency beyond `aiohttp` and PyYAML. Every asset it serves comes from
the UI process itself; there is no CDN and no external resource.

Every module page is generated from that module's manifest (`module.yaml`:
`settings_schema` plus its `title`/`default` annotations, and its trigger
declaration). No module ships HTML: adding a module adds its page.

Where this guide and the Phase 4 specification (`docs/campaigns/phase4/spec.md`)
differ, the specification wins.

## 1. Launching

```sh
python -m core.config_ui --config config.yaml
```

The package also installs the equivalent console script:

```sh
twitch-ia-compagnon-config-ui --config config.yaml
```

Options:

| Option | Meaning |
|---|---|
| `--config PATH` | The base configuration file (required). The UI never writes it. |
| `--overlay PATH` | The managed overlay file (default: derived from `--config`, §3). |
| `--host HOST` | Bind host (default `127.0.0.1`). |
| `--port PORT` | Bind port (default `8765`). |
| `--allow-non-loopback` | Allow a non-loopback `--host` (§2). |
| `--allowed-host NAME` | With a wildcard bind (`0.0.0.0` or `::`), also accept `Host`/`Origin` `NAME:<port>`. Repeatable. |
| `--status-file PATH` | The status record path (default `<base file name>.status.json`, §8). |

### Launch argv after `--`

Everything after `--` on the UI command line is the **launch argv**: the command
Apply runs to start the main process. It is executed directly, **without a
shell** (no quoting, globbing or variable expansion happens):

```sh
python -m core.config_ui --config config.yaml -- /opt/venv/bin/python -m core.main --config config.yaml
```

Without `--`, the launch argv is the interpreter running the UI, executing
`-m core.main --config <base>`, plus the same `--overlay PATH` when one was
given to the UI.

The UI does not start the main process on its own: the first Apply starts it.

## 2. Local-only

The UI is **local-only**: it edits the configuration files of the machine it runs
on. It is never a remote administration channel, and it does **not** administer a
remote brain over the proxy — the proxy protocol (`docs/proxy-protocol.md`)
carries no configuration traffic. To configure the brain on another machine, run
the UI on that machine.

- It binds a loopback address by default. Asking for a non-loopback host
  (`0.0.0.0`, `::`, `192.168.1.10`, …) without `--allow-non-loopback` makes it
  exit with a non-zero status and a diagnostic before binding anything.
- At every start it generates a fresh random access token (at least 128 bits)
  and prints the access URL, which carries the token, **once** on standard
  output:

  ```text
  Configuration UI: http://127.0.0.1:8765/?token=...
  ```

  Open that URL in a browser; the UI then sets a session cookie. A request with
  no valid token or session gets 401/403 and no page content. The token appears
  nowhere else: not in a page, not in a log record. Restarting the UI makes a
  new token; the old URL stops working.
- For a wildcard bind the printed URL uses `127.0.0.1`.
- Every request must carry a `Host` header naming an accepted authority (the
  bound host and port; for a loopback bind also `localhost`, `127.0.0.1` and
  `[::1]` with that port; for a wildcard bind those plus each `--allowed-host`).
  A state-changing request (save, remove-override, check, apply) must be a POST
  with the page's CSRF token, and an `Origin` header, when present, must be
  exactly `http://<accepted authority>`. GET never changes anything.

At most 16 requests are admitted at once (4 running, the rest waiting for a
worker), each body at most 1 MiB. A request beyond that is answered
`503 Service Unavailable` with `Retry-After: 1` before its body is read: nothing
was done, submit it again.

## 3. The overlay file

The UI never writes the base file. Every change goes to a **managed overlay file**
that the runtime merges over the untouched base, so the comments and layout of
the base file stay as you wrote them.

### Overlay path rule

The overlay path is the base file name with a trailing `.example` removed and its
final `.yaml`/`.yml` suffix replaced by `.local.yaml`, in the base file's
directory:

| Base file | Overlay file |
|---|---|
| `config.yaml` | `config.local.yaml` |
| `presence.yaml.example` | `presence.local.yaml` |
| `x.yml` | `x.local.yaml` |
| `config.txt` | none |

`--overlay PATH` overrides this rule, on the UI and on the runtime
(`python -m core.main --config config.yaml --overlay PATH`) alike. A base whose
name ends in neither `.yaml` nor `.yml` has no implicit overlay: the runtime then
reads the base alone, and the UI refuses to start unless `--overlay PATH` is
given. The UI also refuses to start when the overlay path is the base file, with
the diagnostic `overlay_path_collision`, whatever its spelling.

Every path — `--config`, `--overlay`, `--status-file` and the status-file
variable — is used in one canonical form, on the UI and on the runtime alike: `~`
and `$VAR`/`${VAR}` are expanded, the path is made absolute against the working
directory, and symbolic links and `..` are resolved. `~/config.yaml`,
`./sub/../config.yaml` and a link to the base therefore all name the base. A path
that cannot be canonicalised (an unset variable, an unknown `~user`, a link loop)
is refused at startup with the diagnostic `path_not_canonical`; it is never
guessed.

An absent overlay file changes nothing; an empty overlay file means no override.
An overlay that is unreadable, not valid YAML or not a mapping is a configuration
error naming the overlay file (never quoting its content).

### Merge precedence

The overlay is deep-merged over the base before `${NAME}` resolution and
validation:

- a mapping in the overlay merges key by key into the base mapping, recursively;
- any other overlay value — scalar, list or `null` — replaces the base value
  wholesale (lists are not concatenated);
- keys only in the base are kept.

Example: base `{a: {b: 1, c: [1, 2]}, d: x}` and overlay
`{a: {b: 2, c: [3]}, e: y}` give the merged document
`{a: {b: 2, c: [3]}, d: x, e: y}`. An overlay `a: null` yields `a: null` in the
merged document.

Relative paths in the merged configuration resolve from the base file's
directory.

### The overlay cannot delete a base key

A deep merge can override a key the base file defines, but the overlay **cannot
delete a base key**. An entry defined in the base (for example a trigger channel
policy) can be overridden from the UI but not deleted; the page says so instead
of offering a delete that would silently not happen. To delete it, edit the base
file yourself.

"Remove override" deletes a key path from the overlay only; the effective value is
then the base value again.

### The overlay is rewritten by the UI

The overlay is a UI-managed file. Every save rewrites it whole (atomically: a
reader sees the old file or the new one, never a partial one), so **comments and
formatting in the overlay are not preserved**. Put your comments in the base file.
A save is refused when the overlay changed on disk since the page was displayed;
reload the page and redo the edit.

### A stray `*.local.yaml` is merged everywhere

The runtime merges the implicit overlay whether or not the UI is running:
`python -m core.main --config PROFILE --check-config`, `load_config`,
`check_config` and `run` all read it. A `config.local.yaml` (or any
`*.local.yaml`) lying next to a profile — for example a leftover from a UI
session beside a `*.example` profile — is therefore merged by `--check-config`
too, and can change its verdict. Delete or rename it to check the profile alone.
`.gitignore` keeps `*.local.yaml` out of the repository.

## 4. What the UI writes

The UI writes exactly these **5 writable blocks**, into the overlay only:

- `enabled_modules` — one enabled toggle per module, on the base page;
- `modules.<name>` — each module's settings, on that module's page;
- `triggers` — `triggers.<input>.channels.<channel>` (a whole channel policy), on
  the page of the module named `<input>`;
- `limits` — `limits.<group>.<field>`, on the core-settings page;
- `modules_directory` — on the core-settings page.

A boolean module setting is a three-way choice: "not set (default: …)" keeps it
out of the overlay and inherits the declared default, while `true` and `false`
write that value explicitly — so `false` over a `default: true` is saved as
`false`. An explicit value goes back to "not set" through "Remove override". A
configured value that is not a boolean (the string `'true'`, say) is shown as its
own "(not a boolean)" choice and left untouched unless you pick `true` or `false`,
which replaces it with the boolean.

These **2 read-only blocks** are displayed but never written:

- `secrets`
- `actions` (the default-deny authorization rules)

Why (decision 6c): they are read-only in v1 so that a single mis-click can never
widen the set of authorized actions or expose a secret. The pages say so next to
each of them: "read-only in v1: editing could widen the authorized actions /
expose secrets". Edit them in the base file by hand. If you hand-write `secrets`
or `actions` into the overlay, the runtime merges them like any other key, the UI
shows them read-only with origin "overlay", and every UI write keeps them
unchanged.

Any other write is refused and changes nothing: another top-level key, a declared
credential path, a field whose configured value is a `${NAME}` reference, or a
parent of such a field whose write would change it. Values are checked against
the field's own contract (schema, trigger parameter schema, supported combination
operators, limit kind, …) and an invalid save writes nothing. Each accepted save
or removal writes one log record with the time, the page, the operation and the
setting paths — never values.

**Check** validates the merged configuration plus the page's unsaved edits through
the runtime's own check path, without writing anything and without touching the
main process.

## 5. Secrets policy

No secret value appears in any page, response, UI log record, restart report or
diagnostic. Secret values are:

- the resolved value of every environment variable referenced anywhere in the
  base or the overlay (as a value or as a mapping key);
- the values of the variables listed in the `secrets` block;
- any literal value at a declared credential path.

Every such value is covered whatever its length, a single character included,
and wherever the same text is configured: in another setting's value, as a
trigger channel or other named-entry key, in a diagnostic. A configured key that
carries a secret value is named in the page's form controls by an opaque
`@field-…` name the UI maps back, never by its text. Declared names (module,
setting, trigger and limit names) are public text and are shown as they are.

Such fields are displayed only as their reference (`${NAME}`), or as
"literal value configured (hidden)", with their set/unset state. The set/unset
state is evaluated against the UI process's own environment, which is the
environment a main process launched by the UI inherits.

A setting whose value holds such a field, or a secret inside a longer text, is
never offered as one editable text. A list or mapping is edited entry by entry:
each entry keeps its own position, can be removed, moved or (when fully shown)
edited, and a row adds a new entry; a withheld entry is shown masked with an
empty box: left empty it is kept exactly, and anything typed replaces it whole.
A single text setting holding a secret works the same way. Only what changed is
applied to the real configuration the UI loaded, so Check and Save validate
the same values. A submission the UI cannot apply exactly (an entry it does
not know, a stale page, an entry posted twice, an entry both removed and
edited, a new key that already exists) is refused by name and nothing is
written. The UI never edits
environment variables: set them in the shell that starts the UI. The access token
appears only in the single startup line on standard output.

## 6. Apply: restart and supervision

There is no hot reload: applying a change is a **supervised restart**.

1. Apply first runs a Check of the on-disk configuration (base + overlay). If it
   fails, Apply is refused and nothing is stopped or started.
2. The UI stops the main process **it launched itself**: terminate, then wait at
   most the stop grace (10 s), then kill, then wait at most the kill wait (5 s).
   A process still not ended after that is kept supervised (a later Apply or the
   UI's shutdown signals it again), and Apply is refused with a diagnostic naming
   its pid: nothing new is started beside it.
3. It starts the launch argv (§1) without a shell, with the status-file variable
   (§8) set to its status path.
4. It watches the status path for a bounded window (**60 s** by default) and
   reports one of three outcomes:
   - **accepted** — a usable status record whose `pid` is the child just
     launched, whose `state` is `ready` and whose `digest` equals the on-disk
     digest;
   - **refused** — the process exited; its exit status and value-free
     diagnostics are shown;
   - **unknown** — the window elapsed without either. An unusable record never
     counts as accepted.

The UI supervises only a main process it launched (A4). A main process started
elsewhere is reported, labelled **"not supervised"**, and never signalled: stop it
yourself before applying, or two main processes will run. Closing the UI stops
the child it launched.

## 7. Drift: disk vs. running process

Every page shows whether the configuration on disk and the running process agree:

- **in sync** — a usable status record of a live process whose digest equals the
  digest of the current base and overlay files;
- **differs** — a usable record of a live process with another digest (you saved
  since the last restart: Apply to take the change into account);
- **unknown** — no record, an unusable record, or a record whose process is dead.

Each page also shows the record status: "no status record", the record's time, or
**"status record unusable"** with a value-free reason (the failing check and field,
never the file's content). An unusable record is never repaired or partially
trusted: its drift is unknown and it contributes no per-module running state. A
module missing from a usable record shows "not reported by the running process".

## 8. The status file

The main process publishes its state in a status record, a small JSON file it
replaces atomically when it becomes ready and on every later module ready/degraded
transition. It does so only when the environment variable
`TWITCH_IA_COMPAGNON_STATUS_FILE` is set to a path; without it the main process
writes nothing and behaves exactly as before. The record holds no resolved or
credential value, only a digest of the unresolved configuration.

The UI watches exactly one status path:

- `--status-file PATH` when given;
- otherwise the default status path: the base file's full name with
  `.status.json` appended, in the base file's directory — `<base file name>.status.json`,
  e.g. `config.yaml` → `config.yaml.status.json`.

The UI sets `TWITCH_IA_COMPAGNON_STATUS_FILE` to that path for every process it
launches.

**Collision refusal.** The status path must never be a configuration file. It is
compared, as a resolved absolute path (symbolic links followed, whether or not the
file exists yet), with the base file and the managed overlay path. On a match the
UI refuses to start — and `core.main` refuses to start when the variable names such
a path — with the diagnostic `status_path_collision` naming the base or overlay
file, a non-zero exit status, and before binding, launching or writing anything.
The derived default collides only when the overlay is itself named
`<base file name>.status.json` (or links to it); the UI then never picks another
path: pass a non-colliding `--status-file`.

**Making an externally started main process visible.** A main process you start
yourself (not through Apply) is visible to the UI only if you set the variable to
the UI's status path:

```sh
TWITCH_IA_COMPAGNON_STATUS_FILE=config.yaml.status.json \
    python -m core.main --config config.yaml
```

The UI then shows its drift and running state, labelled "not supervised" (its pid
is not the UI's child), and never signals it.
