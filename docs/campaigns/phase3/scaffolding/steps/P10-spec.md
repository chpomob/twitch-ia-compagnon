# Step P10 — `modules/viewer_memory`

Plan step `P10` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Manifest.**
    - Settings: `directory` (required), `retention_days` (1–365, 30),
      `max_files` (1–100000, 1000), `max_file_bytes` (512–65536, 4096),
      `max_total_bytes` (≥ `max_file_bytes`, ≤ 1073741824, 16777216),
      `max_notes` (1–100, 10), `sweep_interval_seconds` (60–86400, 3600),
      `forget_command` (`!forgetme`, empty disables it).
    - The validator checks the cross-field bound.
    - The module consumes `channel.chat.message`. Actions are declared in
      P11. No grant.
  - **File name.** `sha256(json.dumps([platform, channel_id, viewer_id],
    ensure_ascii=False, separators=(",", ":"))).hexdigest() + ".json"`. A JSON
    array is collision-free and no identifier appears in the name.
  - **Content.**
    - `format: 1`, the key fields, `display_name` (cut to 64 characters),
      `first_seen`, `last_seen`, `last_used` (UTC ISO-8601), `use_count`,
      `interactions` and `notes`.
    - Each note has `at`, `viewer_text` ≤ 200, optional `reply_text` ≤ 200
      and `delivery`.
    - Oldest notes are dropped until the file holds ≤ `max_notes` notes and
      ≤ `max_file_bytes` bytes.
  - **Atomic write.** Write a temporary file in the same directory, `fsync`,
    then `os.replace`.
  - **Eviction before a write.** If the count or total would exceed a bound,
    the candidates are every memory file except the target. They are sorted
    by (`last_used`, `use_count`, `first_seen`, name) and deleted in order
    until both bounds hold after the write. One `memory.removed {reason:
    evicted, count}` fact is published per eviction pass.
  - **Retention.** `last_seen` older than `retention_days` (injected clock)
    → deleted at prepare and at each sweep (`expired`), and never returned.
  - **Prepare scan.**
    - Only names matching `^[0-9a-f]{64}\.json$` are read.
    - Malformed or oversized files are deleted (`corrupt`).
    - Expired files are deleted.
    - Eviction runs if the bounds still fail, all before readiness.
    - The sweep runs every `sweep_interval_seconds` as a supervised task.
  - **`!forgetme`.** For a `kind == message` event whose text is exactly
    `forget_command` from a trusted author, delete that author's file for
    the platform and channel (`erased`). This happens whatever the trigger
    decides, because the module consumes the event itself. Facts carry the
    reason and count only.
  - **In-memory index.** An index of metadata keeps eviction ordering
    O(n log n) without re-reading files. It is rebuilt at prepare.
  - **Packaging.** Add the `modules.viewer_memory` pyproject line. Update
    the running catalog counts.

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC13, AC18, AC19
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.
Phase-1 product decision that overrides any contrary reading: delivery is a **configured, pluggable
terminal step** — a configured ordered list of delivery actions (mode `fixed`, or mode `modules`
derived from the enabled modules that declare a delivery capability, with a configurable preference
order), each entry declaring how the answer text maps into its arguments (a named argument, or none
for an effect-only delivery such as a stream-scene change); every entry runs at the terminal step
only, each receiving the text only if declared; the model never selects the delivery and never
performs an intermediate effect; adding a delivery module must require no change to the agentic loop.

## Files

[modules/viewer_memory/__init__.py, modules/viewer_memory/module.yaml, pyproject.toml, tests/test_viewer_memory.py, tests/test_examples.py, tests/test_profiles.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4]

All dependencies are already merged into `main` when this step runs.

## Tests

(the store API is driven directly; the module goes through a
  `runtime_context`)
  - AC13: the name regex matches, the name contains neither `v1` nor `c1`,
    the JSON fields are present, and `(kick, c1)` gives a distinct file.
  - AC14: C is deleted, then the `first_seen` tie-break, then the name
    tie-break. The deletion publishes 1 fact with count 1.
  - AC15: the total-bytes bound holds, and the target is never deleted.
  - AC16: at 30 d + 1 s the record is not returned and is deleted at the
    sweep; at 29 d it is returned; the default is 30.
  - AC17: the 11th note drops the oldest. Over 50 writes of 200-character
    notes with 1024 bytes, every file size is ≤ 1024.
  - AC18: a malformed file and an oversized file are deleted (count 2,
    `corrupt`) and `notes.txt` is untouched; 5 valid files with
    `max_files: 3` leave 3 before `module.ready`.
  - AC19 chat half: `!forgetme` from trusted `v1` gives 1 `erased` fact with
    no id, even with a rejecting policy. `!forgetme please` and a notice
    whose text is `!forgetme` delete nothing.
  - A crash between the temporary write and the replace leaves the old file
    intact (fault-injected `os.replace`).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- A single note larger than the file budget: `viewer_text`/`reply_text`
    are ≤ 200 characters and `max_file_bytes` ≥ 512, so metadata plus one
    note can still exceed 512 bytes with 64 four-byte characters. The rule
    is "drop oldest notes first, and if a lone newest note still does not
    fit, store 0 notes". This keeps the file bound absolute (AC17), and it
    is recorded in the README.
  - Clock skew makes ISO timestamps compare lexicographically only in UTC
    with a fixed format. Pin the format.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase3): ...`, `fix(phase3): ...`, `test(phase3): ...`, `refactor(phase3): ...`).
- Keep the phase-0 guarantees in force: bounded admission, phase lifecycle, explicit terminal action
  outcomes, default-deny authorization (reads included), bounded retention, a single global
  startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss diagnostics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- No new runtime dependency beyond the existing ones unless this step is the one that adds it; no
  model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
