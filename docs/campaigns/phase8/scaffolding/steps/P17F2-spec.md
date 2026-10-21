# P17F2 — The test-preservation gate: restore what the conversions weakened

The closing gate's third finding (F3, HIGH). Option A was chosen by the operator and written into the
spec as **Amendment A2** (AC33, around line 603). Read it first; it defines what is allowed.

## What the gate measured

- **52** existing test functions in 24 files changed their assertion AST; **8** are allowlisted, **44** are
  not. Aggregate assertion counts rose (8892 → 8961) while specific guarantees disappeared, which is why
  counting cannot answer this.
- The allowlisted installation node replaced real installation assertions with package-data globs:
  `assert report['core'].startswith(installed + os.sep)`, the installed `modules_directory` equality and
  `assert launcher.is_file()` are gone.
- `tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base` removed
  the git work-tree and base-commit existence assertions and substituted committed fixture comparisons.
- `tests/test_kick.py` was modified (**89 added / 14 deleted**) although the phase was told not to touch
  it: its loopback transport was replaced.
- One existing node was replaced by a differently named one:
  `tests/test_profiles.py::test_install_test_skips_only_when_pip_is_unavailable` →
  `test_install_test_never_skips_and_fails_loudly`.

## What to do

**1. Restore `tests/test_kick.py` verbatim.** Take the file from the phase base
(`git show 7782e5f:tests/test_kick.py`) and write it back unchanged. It is one of A2's three named
exemptions, so its loopback listener is allowed; it is not converted, not rewritten and not improved here.

**2. Make the three A2 nodes real and exempted by name.** The guard (`tests/conftest.py`) gains a named,
journaled list holding exactly those three node IDs, each allowed to create a socket or start a process
and each recording that it did. The installation node and the AC24 git node get their original assertions
back — installed layout, `startswith(installed + os.sep)`, `modules_directory`, `launcher.is_file()`,
work-tree and base-commit existence — because they now run for real. A2's negative control stays: an
unlisted node that opens a socket or starts a process still raises.

**3. Close the other 41.** For every changed function outside the allowlist, restore its original
assertion sequence. Where a conversion is kept, the fake must be strengthened until it carries the original
assertions exactly (exit status, stdout/stderr, signal, ordering, counts). Where a fake cannot carry an
assertion, **do not** drop it and **do not** add another exemption: report the node and what it needs, and
leave the node unconverted. Finish with the AST comparison clean: zero changed assertion sequences outside
the eight allowlisted functions plus the three A2 nodes.

**4. The replaced node.** Either restore `test_install_test_skips_only_when_pip_is_unavailable` under its
own name, or document in the summary why the replacement is strictly stronger and make the diff visible —
the gate must be able to see it as a deliberate replacement, not a silent one.

## Acceptance

- `git diff --numstat 7782e5f HEAD -- tests/test_kick.py` is empty.
- The AST comparison reports 0 changed existing assertion sequences outside the allowlist and the three A2
  nodes; no test function name removed without a documented replacement.
- The guard refuses an unlisted node that opens a socket or spawns (negative control executed).
- Full suite green: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- No new dependency; no skip/xfail added.

Reply with the per-file restoration summary, the AST comparison counts, the negative-control result, and the
full-suite count.
