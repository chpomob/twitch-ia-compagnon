# Phase 8 — closing gate, third pass

Your second pass resolved F1 and F2 and left F3 open by construction; the operator chose option A for it,
and step P17F2 has landed, with the decision written into the spec as **Amendment A2** (AC33, around
line 603). This pass settles F3 and confirms nothing else moved.

**Write your report to `docs/campaigns/phase8/full-branch-review-3.md`.** Writing THAT ONE FILE is
authorized and required. Create, stage or modify nothing else.

## Settle F3 — the test-preservation gate

Re-run your own measurements, at the new head:

1. **The assertion-AST comparison** across every test file, baseline `7782e5f` versus HEAD, keyed by file
   and qualified test name: it must report **0 changed existing assertion sequences outside the eight
   allowlisted functions and the three A2 nodes**, and no removed function name without a documented
   replacement. State the counts you measure, not the ones the summary claims.
2. **`tests/test_kick.py`** must be byte-identical to the phase base:
   `git diff --numstat 7782e5f HEAD -- tests/test_kick.py` must be empty.
3. **The three A2 nodes** must be real again and exempted **by name**, each journaled when it spawns or
   opens a socket: the installation node asserting the installed layout, `startswith(installed + os.sep)`,
   `modules_directory` and `launcher.is_file()`; the AC24 node asserting the git work tree and base commit;
   the Kick loopback listener untouched. Verify the guard's list holds exactly those three.
4. **The negative control**: an unlisted node that creates a socket or starts a process still raises.
   Execute it, do not infer it.
5. **The 41 other functions**: spot-check at least five of the ones your first pass named, and say whether
   the restored assertions are the originals or stronger ones carried by a faithful fake. Name any node
   that was left unconverted and reported instead.
6. **The replaced node** (`test_install_test_skips_only_when_pip_is_unavailable`): is it restored under its
   own name, or is the replacement visible as a deliberate, documented one?

## Then confirm nothing regressed

- F1 (hard hook bound) and F2 (no transcription before a decision) still hold: re-run one probe each.
- R1–R9 and the 40 criteria: unchanged from your second pass, or state what moved.
- No new skip/xfail/deletion; no new dependency; no socket added outside A2's three names.
- Full suite green: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.

## Deliverable (`docs/campaigns/phase8/full-branch-review-3.md`)

- **VERDICT**: `APPROVE` / `REQUEST_CHANGES` / `REJECT`
- **F3 SETTLED**: RESOLVED / PARTIAL / UNRESOLVED, with the AST counts, the empty Kick diff, the A2 list and
  the executed negative control
- **F1, F2**: still RESOLVED, with the probes you re-ran
- **REGRESSIONS**, **NEW FINDINGS** (executed reproduction only), **NOT VERIFIED**

This gate CLOSES phase 8. If everything you can check passes, say APPROVE plainly.
