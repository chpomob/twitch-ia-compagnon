commit: fix(phase4): P19F12 — ignore the generated overlay and status files

# Step P19F12 — gate-8 finding N8 (LOW): the declared ignore patterns were omitted

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 8 report
`docs/campaigns/phase4/scaffolding/gate-report-8.md` — read **N8** in full, including its reproduction I
and its expected outcome — plus the approved plan's P17 (`docs/campaigns/phase4/plan.md:604`) and the
approved specification (`docs/campaigns/phase4/spec.md:54`).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must not
drop below **3,046 passed / 18 skipped**.

## The defect

P17 required the repository's `.gitignore` to carry the two patterns the feature generates:
`*.local.yaml` (the managed overlay) and `*.status.json` (the status record). They were never added —
`git check-ignore --no-index -v -- config.local.yaml config.yaml.status.json presence.local.yaml` exits 1
with empty output, and `git diff 835d9b4..HEAD -- .gitignore` is empty. An operator following the manual
would commit their own overlay and status file by accident.

## Required

- Add the two patterns to `.gitignore`, with a short comment saying why they exist (they are per-operator
  generated files, not sources).
- Keep the existing entries untouched.
- If the phase-4 plan or specification names any further generated artefact of this feature that is not
  ignored, add it in the same commit and say so in your report; do not add patterns for artefacts that do
  not exist.

## Tests

- A test (in the hygiene suite that already audits the repository layout) asserting that
  `git check-ignore` reports both `config.local.yaml`, `config.yaml.status.json` and a
  `<name>.local.yaml` / `<name>.yaml.status.json` pair as ignored, so the omission cannot silently return.
- Reproduce the gate's command I in your report and show its new output.

## Constraints

- One atomic commit; the message is pinned at the top of this file.
- Report: the lines added, the reproduced `git check-ignore` output, the new test, and the exact suite
  counts.
