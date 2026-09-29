commit: fix(phase4): P19F3 — file-identity collision guard and no configured key in generated names

# Step P19F3 — gate-2 residuals of F1 and F2 (the two HIGH findings)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the phase-4 gate 2 report
`docs/campaigns/phase4/scaffolding/gate-report-2.md` — read **F1** and **F2** in full, including their
"Remaining counterexample" / "Attempts to defeat it" paragraphs and the P/ and C/ evidence, plus the
approved specification (`docs/campaigns/phase4/spec.md`) and the brief (decision 6c).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. The suite must stay
green and must not drop below **2,917 passed / 18 skipped**.

## F1 residual — the guards compare resolved path TEXT, not file identity

Gate 2 confirmed the original counterexample is closed and that `~`, `./`, `..`, relative/absolute
mixtures, environment expansion and a directory symlink are all refused. What remains: **two hard links
to the same file** are not caught. The gate's probe (`/usr/bin/bzip2` and `/usr/bin/bzcat`, one inode)
prints `os.path.samefile(a, b) == True` while `u.same_file(a, b) == False`,
`startup_checks(UISettings(a, overlay=b)) == []` and `status_path_collision(b, a, None) is None`.
Locations: `core/overlay.py:182`, `:217`, `:225`; `core/config_ui/__init__.py:178`, `:246`, `:257`,
`:1925`, `:2886`, `:3724`, `:3884`; the runtime's own check in `core/main.py`.

Required: the collision decision uses **file identity**, not path text — the same underlying file
reached by any name (hard link, symlink, bind-mount alias, differing case on a case-insensitive
filesystem where it applies, or a path that does not exist yet compared against one that does) must be
treated as a collision. Where an identity comparison cannot be made because a path does not exist yet,
fall back to the canonicalised-path comparison and say so in the code. Keep the startup refusal, the
Save destination selection, the status-path rules and the runtime's check on that single decision, and
keep every already-passing refusal green.

## F2 residual — a configured key still reaches generated form names

Gate 2 confirmed the length threshold is gone and that a one-character secret, `q7Z` and the long
credential are all hidden from ordinary configured inputs, channel configuration and error text. What
remains: `core/config_ui/__init__.py:1176` still exempts a segment whose name is treated as structural,
so a configured key occurrence is disclosed through the generated `name=` attributes, e.g.
`name="triggers.twitch.channels.twitch.combination"` or
`name="triggers.twitch.channels.rules.combination"`.

Required: no **configured key occurrence** (a channel id, a rule alias, a module name taken from the
operator's configuration, any list key) may appear in a generated `name=`, `id=`, `for=`, or any other
attribute, label or JSON field. Generate those identifiers from a positional, opaque, deterministic
scheme (index-based) that does not embed the configured text, and use the same scheme in the parser so a
submitted form maps back to the right key. Values the operator legitimately configures and must see stay
visible; only what is a secret value or a key occurrence is withheld. Extend the counterexample into
running tests.

## Constraints

- Fix the production code; never weaken an existing assertion. If a test encodes the residual behaviour,
  invert it and cite the finding in its docstring.
- One atomic commit; the message is pinned at the top of this file.
- Report: for each residual, the `file:line` changed, the identity/identifier scheme you settled, the new
  tests, and the exact suite counts before and after.
