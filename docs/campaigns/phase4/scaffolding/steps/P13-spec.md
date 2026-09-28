# Step P13 — Save and remove-override

Plan step `P13` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Non-trivial (irreversible write to an operator file). Adds `POST /save` and `POST /remove`:
  - **Scope whitelist** of the target key paths:
    - `enabled_modules`;
    - `modules.<name>.<setting path>` (a discovered module);
    - `triggers.<input>.channels.<channel>` (a whole channel policy, where `<input>` is a discovered module declaring trigger types);
    - `limits.<group>.<field>` (in `LIMIT_DECLARATION`);
    - `modules_directory`.
    - Everything else is refused: `secrets`, `actions`, any other top-level key.
  - **Protections:** the target is refused if it is a declared credential path or a field whose configured (merged, unresolved) text is `${NAME}`. For an ancestor target, every protected descendant (credential paths, `${NAME}` values, `${NAME}` mapping keys) must keep exactly its current configured text in the new value (for Save) and must not be deleted (for Remove: removing an override that holds a protected descendant is refused when the effective text would change). Otherwise the operation is refused and changes nothing.
  - **Validation** returns per-field diagnostics and writes nothing:
    - a module setting against its schema node, via `core.contracts` value validation;
    - a trigger policy's rules: the declared type, its `parameter_schema`, and the operator in the supported set;
    - a limit's kind (positive int / finite positive number, excluding bool);
    - `modules_directory` a non-empty string;
    - `enabled_modules` a list of distinct discovered names.
  - **Stale detection:** each rendered form carries the overlay fingerprint (SHA-256 of the file's bytes, or `absent`). A Save or Remove whose fingerprint differs from the current file is refused as stale.
  - **Write:**
    - the new overlay = the current overlay with the edit applied; for Remove, the key deleted and emptied parent mappings pruned;
    - `secrets`/`actions` and every other hand-written key are preserved as parsed (A9);
    - serialized with `yaml.safe_dump(sort_keys=False, allow_unicode=True)`;
    - written atomically: a temp file in the overlay's directory, `fsync`, then `os.replace`;
    - the write target is asserted to be the managed overlay path resolved at startup, and never the base.
  - **Log:** each accepted operation emits one `logging` record on `core.config_ui` with time, page, operation and the touched setting paths, never values.

## Requirements

Plan mapping: R6
Acceptance criteria owned by this step: AC24, AC28, AC10
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

[`core/config_ui/__init__.py`, `tests/test_config_ui.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P12]

All dependencies are already merged into `main` when this step runs.

## Tests

- **AC24:** each of the five saves (module setting, disabling a module, `limits.dedup.max_entries: 2048`, `modules_directory: ./other` with the base page then listing that directory's modules, a new `triggers.twitch.channels.chan2` with a `probability` 0.5 rule) produces an overlay with exactly that override plus the previous ones; `load_config` returns the new value; there is one log record with the paths and no value.
  - **AC25:** removing `modules.M.S` restores the base value and prunes `modules.M`/`modules`; a base-defined channel policy offers no delete action and states the reason.
  - **AC26:** each invalid save (out-of-range, wrong type, `max_entries: 0`, `ttl_seconds: "x"`, an empty `modules_directory`, a duplicate or unknown enabled name, an undeclared rule type, `probability: 1.5`, an unsupported operator) returns a per-field diagnostic with the overlay bytes unchanged; a stale save is refused.
  - **AC27:**
    - writes to `secrets`, `actions` (add, change, remove), another top-level key, a credential field or a `${NAME}` field are all refused with no file change;
    - a hand-written `actions` rule keeps the same parsed value after an unrelated save;
    - the ancestor cases for `modules.M.o`: `{k:"${VAR_K}", n:2}` accepted and keeping the reference; `{k:"plain", n:2}` and `{n:2}` refused;
    - the credential-literal ancestor remove is refused;
    - the twitch `${TWITCH_BROADCASTER_ID}` channel-policy save keeps the key as reference text.
  - **AC28:** `os.replace` patched to raise leaves the previous overlay intact and parseable, with no temp file left.
  - **AC10:** after every AC22–AC28 operation, the base bytes are unchanged (asserted in a shared fixture teardown).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- YAML round-tripping loses the operator's comments and formatting in the overlay. Accepted: the overlay is UI-managed, and this is documented in P18.
  - `yaml.safe_dump` of a `${NAME}` string must quote it correctly, and a round-trip must preserve it.
  - The ancestor rule for `${NAME}` mapping keys (channel keys) must compare *key text*, not resolved values.
  - A TOCTOU race between the fingerprint check and the replace is accepted (single local operator).

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase4): ...`, `fix(phase4): ...`, `test(phase4): ...`, `refactor(phase4): ...`, `docs(phase4): ...`).
  The scope MUST be `phase4`: the repository history is full of `feat(phase3):` / `fix(phase2):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase4)`.
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
