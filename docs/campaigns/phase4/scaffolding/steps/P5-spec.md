# Step P5 — Manifest titles and defaults

Plan step `P5` of the approved Phase 4 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase4/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase4/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase4/brief.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Titles:** for each listed manifest, add a non-empty `title` to every setting node of `settings_schema`: the root, each `properties` entry recursively, and each `items` sub-schema having `properties`.
  - **Defaults:** for every node whose `description` contains `Default <literal>.` (a bare or backtick-quoted YAML value, or `empty`), add `default:` equal to that literal parsed as YAML, or the empty value of the node's type for `empty`.
  - Descriptions stay unchanged.
  - Extend `tests/test_manifest_presentation.py` with:
    - a module-level `PRESENTED` tuple listing the manifests completed so far;
    - a node walker;
    - a `Default <literal>.` extractor, using the regex ``Default (`[^`]*`|\S+?)\.(\s|$)``, with `empty` mapped by type;
    - the parametrised tests over `PRESENTED`.

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC12, AC13
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

[`modules/agent_link/module.yaml`, `modules/audio_input/module.yaml`, `modules/audio_output/module.yaml`, `modules/audit/module.yaml`, `tests/test_manifest_presentation.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1]

All dependencies are already merged into `main` when this step runs.

## Tests

For each manifest in `PRESENTED`:
  - AC12: the count of checked nodes is > 0 and every node has a non-empty string title.
  - AC13: every documented default is present and equal; the audio_output `max_text_chars` default is `400`; every declared `default` passes `validate_schema`.
  - Run `tests/test_audio_output.py`, `tests/test_audio_input.py`, `tests/test_audit.py`, `tests/test_examples.py` and `tests/test_loader.py`.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 2,460 passed / 18 skipped.

## Risks

- Other tests compare schema nodes as exact dicts. Grep `settings_schema\]\[.properties.\]` / `== {"type"` in the tests of these modules before editing. Any failure outside the spec's allowlist must be flagged, not weakened.
  - A description with prose like "Default behaviour." is not a literal default; the extractor pattern must not match it. When in doubt, check the prose manually.
  - `audio_output` `voices` has a property named `default`. Do not confuse it with the annotation.

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
