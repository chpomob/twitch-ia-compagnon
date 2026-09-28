commit: fix(phase4): P19F1 — overlay collision by any spelling of the path; the secret guard covers every value length

# Step P19F1 — gate findings F1 and F2 (the two HIGH defects)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 closing gate
report `docs/campaigns/phase4/scaffolding/gate-report-1.md` — read **F1 and F2 in full**, including
their executed counterexamples (C/F1, C/F2a, C/F2b), and the approved specification
(`docs/campaigns/phase4/spec.md`) and brief (`docs/campaigns/phase4/brief.md`, decisions 2a/3a/6c).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (project virtualenv;
the system python lacks `aiohttp`). The suite must stay green and must not drop below 2,889 passed /
18 skipped.

## F1 — HIGH — a tilde overlay path bypasses collision checks and Save targets the base

`UISettings.overlay_path` and `_managed_overlay` retain the raw path; `same_file()` resolves it without
expanding `~`, while `_write_overlay()` finally writes wherever that path points. A `--overlay
~/config.yaml` (or any spelling that is not textually identical to the base path) therefore slips past
the collision guard, and **Save writes into the base configuration file**, destroying the file the whole
design promises never to touch. Locations cited by the gate: `core/config_ui/__init__.py:180`, `:1784`,
`:223`, `:3561`; `core/overlay.py:162`.

Required: the collision decision must be made on **one canonical form** of every path — expand `~` and
the environment, make absolute, resolve symlinks and `..` — computed in exactly one place and used by
the startup refusal, the Save destination selection, the status-path rules and the runtime's own check.
Any overlay path that canonicalises to the base configuration file must be refused at startup with a
named diagnostic and a non-zero exit status **before binding or writing**, and Save must never choose
the base file as its destination, whatever spelling was configured. Do not special-case `~`: expand
and canonicalise uniformly, and prefer refusing over guessing when a path cannot be canonicalised.

## F2 — HIGH — R8 is bypassed by short secrets and configured keys treated as structural identifiers

The specification's rendering guard requires that no secret **value** appears in a page or response,
for values of length **≥ 1**. The implementation skips every value shorter than four characters and,
separately, treats a configured key as a structural identifier, so the same text can surface through a
setting or a trigger channel key. Gate counterexamples: a 3-character `${GATE_SECRET}` set to `q7Z` and
also placed in `modules.twitch.companion_name` returns 200 with the value visible; a literal
`modules.twitch.client_secret` value used as a trigger channel key is likewise visible. Locations:
`core/config_ui/__init__.py:1063`, `:1070`, `:1131`, `:2281`.

Required: the guard covers **every** secret value the configuration can hold, at **any** length —
including a single character — with no length threshold and no exemption for a value that also happens
to appear as a key, a channel identifier, a label or any other structural position. A value that came
from a declared secret setting must never be rendered, echoed, logged, or reflected in an error, page
or JSON response, in any field, at any nesting level. Keep the honest behaviour for the *reference* and
the set/unset state: those stay visible. Convert both counterexamples into running tests.

## Constraints

- Fix the production code; never weaken an existing assertion. If a test encodes the defective
  behaviour, invert it and cite the finding in the test docstring.
- One atomic commit for the whole step; the message is pinned at the top of this file.
- Report: for each finding, the `file:line` changed, the canonicalisation or guard shape you settled,
  the new tests, and the exact suite counts before and after.
