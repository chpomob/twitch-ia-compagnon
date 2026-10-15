#!/usr/bin/env python3
"""Generate per-step loop specs (Pn-spec.md) for PHASE 5 from the approved plan.

Durable copy: this script and its output live INSIDE the repository
(`docs/campaigns/phase5/scaffolding/`) so a reboot or a /tmp wipe cannot destroy the campaign
scaffolding (2026-09-16: a reboot erased /tmp and the whole runner + step specs with it).

Mechanical extraction — no rewriting: each step spec carries the plan's own
Files/Description/Dependencies/Tests/Risks text plus the spec's R/AC references, and the loop
constraints (test command, atomic commit, no weakened tests).

Root-document guard: a step may list the repository-root `spec.md` or `plan.md` as a target. Those
are the historical v1-MVP documents (the root `spec.md`/`plan.md` alias the phase 1 archive). Every
root-document target is rewritten to the phase-5 archive under `docs/campaigns/phase5/`, with an
explicit do-not-overwrite note.
"""
import pathlib
import re

REPO = '/media/chpo/HDD-papa/twitch-ia-compagnon'
BASE = pathlib.Path(f'{REPO}/docs/campaigns/phase5')
PLAN = BASE / 'plan.md'
SPEC = BASE / 'spec.md'
OUT = BASE / 'scaffolding/steps'

ROOT_GUARD = (
    '\n\n**Do not overwrite the repository-root `spec.md` / `plan.md`**: those are the frozen v1-MVP\n'
    'documents (the root `spec.md` is the phase 1 specification the documentation index aliases).\n'
    'The phase-5 specification and plan are `docs/campaigns/phase5/spec.md` and\n'
    '`docs/campaigns/phase5/plan.md` — never write the phase-5 documents to the repository root.'
)

STANDING_DECISIONS = (
    'Standing phase-5 decisions that override any contrary reading of a requirement:\n'
    '- **The bridge is optional in every case.** Absent, disabled or unreachable, the companion behaves\n'
    '  exactly as it does today: no setting becomes required at startup, no default path changes, and no\n'
    '  shipped profile depends on it. An unreachable instance produces a *ready* companion with an\n'
    '  explicitly unavailable capability — never a failed startup.\n'
    '- **The minimum scope is the sink and the triggers, together**: the sink runs a named action of the\n'
    '  user\'s instance behind our own authorization, deadlines and honest terminal outcomes; the triggers\n'
    '  let an event the instance routes enter the companion through the ordinary trigger policies.\n'
    '- **Its events may REPLACE our basic inputs (chat, viewers) — never in parallel.** For every\n'
    '  (family, platform) pair there is at most one active provider, resolved at preparation from the\n'
    '  profile and the manifests, and an ambiguous configuration is refused with a diagnostic naming the\n'
    '  family and both providers. Counting on the deduplication window to hide two providers is excluded.\n'
    '- **Do not hide the integration\'s defects.** An execution the instance cannot confirm is never\n'
    '  reported as a success: an acknowledged run ends `external_unknown` with its cause, and the model\n'
    '  and the operator both see it.\n'
    '- **This phase is NOT "delivered" at merge.** The README and the documentation index carry\n'
    '  "not delivered — awaiting real-instance trial" until the trial record exists, and a test enforces it.\n'
    '- **The vendor and product names never appear in `core/`.** The kernel vocabulary stays vendor-free:\n'
    '  action `automation.action.run`, permission `automation.run`, destination scope `automation`,\n'
    '  families `chat`/`viewers`/`cues`.\n'
    '- **The approved plan\'s deviations D1–D8 are decisions of this phase** and override any contrary\n'
    '  reading of the specification — including the two files beyond the specification\'s cost row:\n'
    '  `core/actions.py` (`excluded_platforms`, D5) and `core/triggers.py` (`add_companion_identities`,\n'
    '  D6). The D8 transmission-boundary canary must stay green; if an aiohttp upgrade breaks it, take the\n'
    '  fallback route D8 documents and say which route you took in the commit message.\n'
    '- The configuration UI stays a **local-only** surface; the ownership table and its diagnostics are\n'
    '  displayed **read-only**, and nothing in the UI can widen the authorization set or write the\n'
    '  `secrets` and `actions` blocks.\n'
)


def rewrite_root_targets(files: str) -> str:
    """Point any bare root spec.md/plan.md target at the phase-5 documents."""
    def sub(m):
        name = m.group(2)
        if name in ('spec.md', 'plan.md'):
            return f'`docs/campaigns/phase5/{name}`'
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

Plan step `{sid}` of the approved Phase 5 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The integration study the brief is grounded in: `docs/research/streamerbot-etude-2026-10.md`
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
  (`feat(phase5): ...`, `fix(phase5): ...`, `test(phase5): ...`, `refactor(phase5): ...`, `docs(phase5): ...`).
  The scope MUST be `phase5`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase5)`.
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
