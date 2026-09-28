# Step P14 — Status-record reader, drift and running state (R7 reader side, R4 readiness; AC39, AC40, AC42, AC21 readiness, AC31 display, AC32 display, AC33 reader check)

Plan step `P14` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial (parsing untrusted input, classification branching).
  - **`read_status(path) -> StatusReading`**, where `StatusReading` is one of `absent`, `unusable(reason)` or `usable(record)`:
    - `os.stat` → a regular file ≤ 1 MiB, otherwise unusable;
    - read the bytes; UTF-8 strict decoding;
    - `json.loads` with `object_pairs_hook`, which rejects duplicate keys, and `parse_constant`, which rejects `NaN`/`Infinity`/`-Infinity`;
    - the top level must be a dict holding all 8 required fields;
    - typed checks:
      - integer = `type(v) is int` (excludes bool and float); `version == 1`; `pid` in 1..2147483647; `sequence` in 1..2^53−1;
      - timestamps by regex `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$` plus `datetime` construction for calendar validity;
      - `digest` by `^[0-9a-f]{64}$`;
      - `state == "ready"`;
      - `modules` a dict with non-empty string keys whose values are dicts with `state` in `{ready, degraded}`.
    - Extra keys are ignored.
    - The reason is value-free: the failing check name, the field name and the expected type or set.
    - No coercion, no partial trust.
  - **Liveness:** `os.kill(pid, 0)`: `ProcessLookupError` → dead; success or `PermissionError` → alive. For the UI's own child, `Popen.poll()` is authoritative. Both are behind an injectable `probe` seam.
  - **Drift:**
    - `unknown` for absent, unusable or dead records;
    - otherwise `in sync` if `record.digest == core.overlay.on_disk_digest(base, overlay)`, else `differs`.
  - **Running state:** per module, from the usable, live record's container. A module with no entry shows "not reported by the running process"; entries naming no discovered module are not displayed. The time shown is `published_at`.
  - **Supervision label:** a record whose pid is not the launched child's pid → "not supervised".
  - Every page (base, core, module) shows the record status ("no status record" / "status record unusable: <reason>" / the record time), the drift state and the supervision label. The base page's readiness column combines the last Check verdict and the running state; the module page shows it too.
  - Add the reader-side assertion to AC33 in `tests/test_status_record.py`: each of the 3 published records classifies as usable.

## Requirements

Plan mapping: R7, R4
Acceptance criteria owned by this step: AC39, AC40, AC42, AC21, AC31, AC32, AC33
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-4 decisions that override any contrary reading of a requirement:
- The configuration UI runs in a **separate process** (`python -m core.config_ui`); it is a
  **local-only** surface (loopback by default, an access token, a Host/Origin and CSRF guard),
  never a remote administration channel, and it adds **no runtime dependency** beyond aiohttp
  and PyYAML, with no CDN or external asset.
- Its writing scope (decision 6c) is exactly `enabled_modules`, `modules.<name>`, `triggers`,
  `limits` and `modules_directory`. The `secrets` block and the `actions` block (the default-deny
  authorization rules) are **read-only in v1 and displayed read-only**, so a single mis-click can
  never widen the set of authorized actions.
- Writes go to a **managed overlay file** merged over the untouched base file (comments preserved);
  a secret VALUE never appears in any page, response, log or diagnostic, only its reference and its
  set/unset state.
- Every module page is **generated from the module manifest** (schema plus presentation metadata);
  **no module ships HTML**, and adding a module must add its page with no UI code change.
- Applying a change is a **supervised restart** (there is no hot reload in this phase).

## Files

[`core/config_ui/__init__.py`, `tests/test_config_ui.py`, `tests/test_status_record.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4, P13] (P13 because the AC32 display test performs an overlay Save, and because P12, P13 and P14 all edit `core/config_ui/__init__.py` and `tests/test_config_ui.py`, so they must run in that order; P13 transitively brings P11 and P12)

All dependencies are already merged into `main` when this step runs.

## Tests

- **AC39:** a record with mode 000 (skipped when running as root, with the reason stated), a directory at the path, non-JSON bytes, `[1,2]`, a missing digest, and pid `"123"` with a matching digest. For each, every page returns 200 and shows "status record unusable" with a reason different from the "no status record" text and containing none of the record's bytes; drift is unknown and no running state is shown.
  - **AC40:**
    - the exact mutation list (8 removals; the version, pid, started_at, published_at, sequence, digest, state and modules mutations; duplicate pid; NaN; 1 MiB + 1 byte), each unusable, never "in sync", pid never displayed;
    - V usable, and V with extra keys usable with the same display;
    - V with a dead pid → unknown (the probe seam returns dead).
  - **AC42:** records with `modules` `{}`, one with an extra `Z`, `sequence` 7, and `started_at` later than `published_at`: all usable and "in sync"; M/N show "not reported by the running process" where they have no entry; Z appears nowhere. With a differing digest → "differs".
  - **AC21 readiness:** M degraded in a record → the base page shows degraded plus the record time; with no record → only the Check verdict plus "no running process is known".
  - **AC31 display:** a record from a live foreign pid (the test process's own pid, which is not a child) shows per-module state labelled "not supervised"; a record at another path is not read; the default path is `config.yaml.status.json`; `--status-file` overrides it.
  - **AC32 display:** a matching digest → "in sync" on all pages; after an overlay save changing a value → "differs"; no record or a dead pid → "unknown". The Apply leg is in P15.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- `json.loads` accepts `1.0` as float and `1` as int. `type(v) is int` correctly rejects `1.0`, `1e0` and `true`.
  - An oversized file must be checked by `stat` *before* reading, with the read also capped at 1 MiB + 1 byte to handle a race.
  - Mode-000 tests fail as root. Add a `pytest.skip` only for `os.geteuid() == 0`, alongside the directory case, which always runs.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase4): ...`, `fix(phase4): ...`, `test(phase4): ...`, `refactor(phase4): ...`, `docs(phase4): ...`).
- Keep the phase-0 to phase-3 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, and the phase-3 moderation/memory/watch semantics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
