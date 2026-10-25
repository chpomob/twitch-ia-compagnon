#!/usr/bin/env python3
"""Generate per-step loop specs (Pn-spec.md) for PHASE 10 from the approved plan.

Durable copy, inside the repository (`docs/campaigns/phase10/scaffolding/`): the 2026-09-16 reboot
wiped /tmp and destroyed a whole runner, its step specs and its logs.

Mechanical extraction — no rewriting: each step spec carries the plan's own Files/Description/
Dependencies/Tests/Risks text, plus the phase's standing decisions and the loop constraints.
"""
import pathlib
import re

REPO = "/media/chpo/HDD-papa/twitch-ia-compagnon"
BASE = pathlib.Path(f"{REPO}/docs/campaigns/phase10")
PLAN = BASE / "plan.md"
OUT = BASE / "scaffolding/steps"

STANDING = """\
Standing phase-10 decisions that override any contrary reading of a requirement:

- **No new runtime dependency and no second image.** The image installs the project's own
  distribution; whatever the container needs beyond it is a build-time tool, not a runtime one.
- **Secrets are supplied, never baked.** No image layer, environment variable, label or default
  configuration may carry a credential, a key, a token or one of the operator's profiles. The
  `${...}` reference mechanism stays the only way a value enters a configuration. A hidden file
  (`.env`, `*.instance.json`, `*.supervisor.json`, anything under the state directory) must never
  reach the build context.
- **The ordinary suite stays green with no Docker daemon.** Checks that read files (the Dockerfile's
  declared properties, the compose file's shape, `.dockerignore`'s coverage) are ordinary tests.
  Anything that needs a daemon is a trial check: it runs where a daemon exists (this machine, the
  `container-trial` CI job) and the ordinary suite neither skips silently nor pretends it ran. A new
  exemption follows phase 9's Amendment A2 terms: one named class, enumerated, asserted by an
  invariant test.
- **`/instance` stays session-free** (token, no session, marker and version only), exactly as phase 9
  defined it; every other route requires a session. The container's healthcheck uses the probe.
- **One volume for all state**, documented file by file, and a restored volume must not change
  behaviour silently.
- **A state the health command cannot compute from the records it reads is not one of its states.**
  Remove it or make it observable — never guess.
- **Nothing is published**: no registry push, no tag, no release, no GitHub release. Building,
  running and measuring locally is the whole phase.
- Never weaken an existing test to make a step pass. If an existing test must change, the plan names
  it, the change is an added expectation (an allowlist entry), and silently dropping an assertion is
  a rejection.

Loop constraints: run `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` before committing;
one atomic commit per step; no `git push`; no edit to `docs/campaigns/phase10/{spec,plan}.md`.
"""


def blocks(text):
    """Yield (id, title, body) for every `### P<n>: title` block."""
    pat = re.compile(r"^### (P\d+):\s*(.+)$", re.M)
    marks = list(pat.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end]
        # stop at the next level-2 section (e.g. "## Acceptance", "## Risks")
        cut = re.search(r"^## (?!#)", body, re.M)
        if cut:
            body = body[:cut.start()]
        yield m.group(1), m.group(2).strip(), body.strip()


def main():
    text = PLAN.read_text(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for sid, title, body in blocks(text):
        doc = (
            f"# {sid} — {title}\n\n"
            f"Plan: `docs/campaigns/phase10/plan.md`, step {sid}. Specification:\n"
            f"`docs/campaigns/phase10/spec.md`. Brief: `docs/campaigns/phase10/brief.md`.\n\n"
            f"## The plan's own words\n\n{body}\n\n"
            f"## {STANDING}"
        )
        path = OUT / f"{sid}-spec.md"
        path.write_text(doc, encoding="utf-8")
        written.append((sid, len(doc.splitlines())))
    for sid, n in written:
        print(f"{sid}: {n} lines")
    print(f"{len(written)} step specs written to {OUT}")


if __name__ == "__main__":
    main()
