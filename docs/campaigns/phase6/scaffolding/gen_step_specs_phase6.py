#!/usr/bin/env python3
"""Generate per-step loop specs (Pn-spec.md) for PHASE 5 from the approved plan.

Durable copy: this script and its output live INSIDE the repository
(`docs/campaigns/phase6/scaffolding/`) so a reboot or a /tmp wipe cannot destroy the campaign
scaffolding (2026-09-16: a reboot erased /tmp and the whole runner + step specs with it).

Mechanical extraction — no rewriting: each step spec carries the plan's own
Files/Description/Dependencies/Tests/Risks text plus the spec's R/AC references, and the loop
constraints (test command, atomic commit, no weakened tests).

Root-document guard: a step may list the repository-root `spec.md` or `plan.md` as a target. Those
are the historical v1-MVP documents (the root `spec.md`/`plan.md` alias the phase 1 archive). Every
root-document target is rewritten to the phase-6 archive under `docs/campaigns/phase6/`, with an
explicit do-not-overwrite note.
"""
import pathlib
import re

REPO = '/media/chpo/HDD-papa/twitch-ia-compagnon'
BASE = pathlib.Path(f'{REPO}/docs/campaigns/phase6')
PLAN = BASE / 'plan.md'
SPEC = BASE / 'spec.md'
OUT = BASE / 'scaffolding/steps'

ROOT_GUARD = (
    '\n\n**Do not overwrite the repository-root `spec.md` / `plan.md`**: those are the frozen v1-MVP\n'
    'documents (the root `spec.md` is the phase 1 specification the documentation index aliases).\n'
    'The phase-6 specification and plan are `docs/campaigns/phase6/spec.md` and\n'
    '`docs/campaigns/phase6/plan.md` — never write the phase-6 documents to the repository root.'
)

STANDING_DECISIONS = (
    'Standing phase-6 decisions that override any contrary reading of a requirement:\n'
    '- **Every phase-5 decision stays binding.** The bridge is optional in every case; its events\n'
    '  replace the basic inputs and never run beside them; an execution the instance cannot confirm is\n'
    '  never reported as a success; authorization is default-deny; an input pair has exactly one owner.\n'
    '- **The fix is the finding, nothing more.** Close exactly the findings this step names. If a\n'
    '  sibling path shows the same defect, report it in your final message instead of widening the\n'
    '  step: the plan has a step for it, and a fix that edits another step\'s files is refused.\n'
    '- **No test is weakened.** Nothing is skipped, xfailed, deleted or loosened. A fix ships with the\n'
    '  regression that fails without it: for a product defect the test fails on `8010edc`; for a defect\n'
    '  in a test (F27, F28, F32) the new assertion is proven able to fail by a stated mutation of the\n'
    '  code under test. Only a test named in the specification\'s failure allowlist may have its\n'
    '  assertions replaced, and each replacement carries a docstring citing its finding and requirement.\n'
    '- **Nothing changes for a shipped profile beyond the finding.** No new required setting, no new\n'
    '  dependency, no changed default path -- except where the finding IS that default (the installation\n'
    '  guide\'s endpoint value, the README command, the overlay file name).\n'
    '- **The bridge stays "not delivered".** `tests/test_phase5_docs_status.py` passes unchanged.\n'
    '- The full suite is green at every step: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`\n'
    '  (never the system python, which lacks aiohttp).\n'
)


def rewrite_root_targets(files: str) -> str:
    """Point any bare root spec.md/plan.md target at the phase-6 documents."""
    def sub(m):
        name = m.group(2)
        if name in ('spec.md', 'plan.md'):
            return f'`docs/campaigns/phase6/{name}`'
        return m.group(0)
    return re.sub(r'(`)([^`]+\.md)`', sub, files)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    plan = PLAN.read_text(encoding='utf-8')
    chunks = re.split(r'^### (P\d+): (.*)$', plan, flags=re.M)
    items = [(chunks[i], chunks[i + 1].strip(), chunks[i + 2]) for i in range(1, len(chunks), 3)]

    written = []
    for sid, title, body in items:
        def grab(field):
            m = re.search(rf'\*\*{field}:\*\*\s*(.*?)(?=\n- \*\*|\Z)', body, re.S)
            return m.group(1).strip() if m else '(not specified in plan)'

        files = grab('Files')
        desc = grab('Description')
        deps = grab('Dependencies')
        tests = grab('Tests')
        risks = grab('Risks')
        reqs = ', '.join(re.findall(r'R\d+', title)) or '(see plan)'
        acs = ', '.join(re.findall(r'AC\d+', title)) or '(see plan)'
        title_short = title.split('—')[0].strip() if '—' in title else title

        guarded = rewrite_root_targets(files)
        guard_note = ROOT_GUARD if guarded != files else ''

        content = f"""# Step {sid} — {title_short}

Plan step `{sid}` of the approved Phase 6 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase6/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase6/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase6/brief.md`
- The full adversarial review whose findings this phase closes: `docs/reviews/2026-10-16-full-adversarial-review.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `{REPO}` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

{desc}

## Requirements

Plan mapping: {reqs}
Acceptance criteria owned by this step: {acs}
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

{STANDING_DECISIONS}
## Files

{guarded}{guard_note}

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

{deps}

All dependencies are already merged into `main` when this step runs.

## Tests

{tests}

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 3,124 passed / 18 skipped, the count at the start of
this phase; the seven allowlisted tests are adapted as the specification lists them, never weakened.

## Risks

{risks}

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase6): ...`, `fix(phase6): ...`, `test(phase6): ...`, `refactor(phase6): ...`, `docs(phase6): ...`).
  The scope MUST be `phase6`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase6)`.
- Keep the phase-0 to phase-4 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, the phase-3 moderation/memory/watch semantics, and the phase-4 configuration UI's
  local-only, read-only-for-secrets-and-actions surface.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
"""
        (OUT / f'{sid}-spec.md').write_text(content, encoding='utf-8')
        written.append(sid)

    print('written:', len(written), 'step specs ->', OUT)
    print('ids:', ' '.join(written))
    small = [s for s in written if (OUT / f'{s}-spec.md').stat().st_size <= 900]
    print('suspiciously small:', small or 'none')


if __name__ == '__main__':
    main()
