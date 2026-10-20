#!/usr/bin/env python3
"""Generate per-step loop specs (Pn-spec.md) for PHASE 5 from the approved plan.

Durable copy: this script and its output live INSIDE the repository
(`docs/campaigns/phase7/scaffolding/`) so a reboot or a /tmp wipe cannot destroy the campaign
scaffolding (2026-09-16: a reboot erased /tmp and the whole runner + step specs with it).

Mechanical extraction — no rewriting: each step spec carries the plan's own
Files/Description/Dependencies/Tests/Risks text plus the spec's R/AC references, and the loop
constraints (test command, atomic commit, no weakened tests).

Root-document guard: a step may list the repository-root `spec.md` or `plan.md` as a target. Those
are the historical v1-MVP documents (the root `spec.md`/`plan.md` alias the phase 1 archive). Every
root-document target is rewritten to the phase-7 archive under `docs/campaigns/phase7/`, with an
explicit do-not-overwrite note.
"""
import pathlib
import re

REPO = '/media/chpo/HDD-papa/twitch-ia-compagnon'
BASE = pathlib.Path(f'{REPO}/docs/campaigns/phase7')
PLAN = BASE / 'plan.md'
SPEC = BASE / 'spec.md'
OUT = BASE / 'scaffolding/steps'

ROOT_GUARD = (
    '\n\n**Do not overwrite the repository-root `spec.md` / `plan.md`**: those are the frozen v1-MVP\n'
    'documents (the root `spec.md` is the phase 1 specification the documentation index aliases).\n'
    'The phase-7 specification and plan are `docs/campaigns/phase7/spec.md` and\n'
    '`docs/campaigns/phase7/plan.md` — never write the phase-7 documents to the repository root.'
)

STANDING_DECISIONS = (
    'Standing phase-7 decisions that override any contrary reading of a requirement:\n'
    '- **Wikipedia works with no key; Brave is an option.** A profile with no credential must get a\n'
    '  working Wikipedia search; Brave is configured, never required, and its key is a `${...}` reference.\n'
    '- **Two actions only**, `web.search` and `web.read`. No combined action and no separate\n'
    '  `wikipedia.summary`: Wikipedia is a provider behind the search contract.\n'
    '- **The network guard is the only way out.** Every request, at every hop, goes through R4: no\n'
    '  loopback, private, link-local or non-public address, redirects re-checked, the byte bound measured\n'
    '  while streaming, the whole call bounded, and no proxy environment variable may ever be honoured.\n'
    '- **Every bound is a setting with a validated range** (R5), and nothing returns more than its bound.\n'
    '- **The query is never written.** A query may be a viewer\'s words: traces and diagnostics carry the\n'
    '  provider, lengths, counts and the outcome, never the text. The in-memory cache key is not a record;\n'
    '  it never leaves the process.\n'
    '- **Read content is data.** The observation carries the untrusted marker, the brain renders it as\n'
    '  data, and no configuration of this module can grant a capability: default-deny is untouched.\n'
    '- **No new runtime dependency.** HTML becomes text with the in-module extractor; if a library looks\n'
    '  unavoidable, stop and report instead of adding one.\n'
    '- **No test opens a real socket.** Every test uses a fake transport; the real-instance trial is a\n'
    '  documented manual procedure with its own record, never a pytest node.\n'
    '- Close exactly the requirement(s) this step names. If a sibling path shows the same defect, report\n'
    '  it in your final message instead of widening the step.\n'
    '- No test is skipped, xfailed, deleted or loosened. The full suite is green at every step:\n'
    '  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (never the system python: no aiohttp).\n'
)


def rewrite_root_targets(files: str) -> str:
    """Point any bare root spec.md/plan.md target at the phase-7 documents."""
    def sub(m):
        name = m.group(2)
        if name in ('spec.md', 'plan.md'):
            return f'`docs/campaigns/phase7/{name}`'
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

Plan step `{sid}` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
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
  (`feat(phase7): ...`, `fix(phase7): ...`, `test(phase7): ...`, `refactor(phase7): ...`, `docs(phase7): ...`).
  The scope MUST be `phase7`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase7)`.
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
