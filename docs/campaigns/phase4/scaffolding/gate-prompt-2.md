# Phase 4 — full-branch closing gate, SECOND pass (after the F1–F5 fix round)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Gate 1
(`docs/campaigns/phase4/scaffolding/gate-report-1.md`) returned **REQUEST_CHANGES** with five findings,
each carrying an executed counterexample: F1 and F2 (HIGH) and F3, F4, F5 (MEDIUM). Two fix steps have
since landed.

**Write your report to `docs/campaigns/phase4/scaffolding/gate-report-2.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file. Review the whole branch diff, not one commit at a time.

For every finding: reproduce first, then judge. Reuse the counterexample the first gate published for it
(C/F1, C/F2a, C/F2b, C/F3, C/F4, C/F5) plus its own listed reproduction, and report what it now does.

## Your job

1. **Verify each of F1–F5**, from the code AND a running test, citing `file:line` and the executed test
   name. Status per finding: `RESOLVED`, `PARTIALLY RESOLVED` or `NOT RESOLVED`.
   - **F1 (HIGH)** — a tilde (or otherwise differently spelled) overlay path bypassed the collision
     checks and Save targeted the base configuration file. Required: one canonical form of every path,
     computed in one place and used by the startup refusal, the Save destination, the status-path rules
     and the runtime; any overlay that canonicalises to the base is refused before binding or writing.
     Try to defeat it: `~`, `./`, `..`, a symlink, a hard link, a relative versus absolute mixture, a
     path through an environment variable.
   - **F2 (HIGH)** — the secret-value guard skipped values shorter than four characters and exempted
     configured keys treated as structural identifiers, so a secret could be rendered. Required: the
     guard covers every secret value at any length, in any position, with no length threshold and no
     structural exemption. Try to leak a 1-character secret, a value reused as a key, a channel
     identifier, a label, and a value appearing inside an error message.
   - **F3 (MEDIUM)** — a boolean setting absent from the configuration with `default: true` rendered
     unchecked, so an explicit `false` draft vanished. Required: the effective value is rendered and the
     draft expresses unset / false / true distinctly, writing `false` to the overlay.
   - **F4 (MEDIUM)** — the dispatch path enqueued unbounded work in the executor's queue before the host,
     session and CSRF checks ran. Required: bounded queued work, an honest refusal, and a bounded body
     read.
   - **F5 (MEDIUM)** — `Supervisor.stop`'s post-kill `child.wait()` had no timeout. Required: every wait
     bounded, an unkillable child ending in an explicit reported outcome, consistent with the restart
     path's 60-second accounting.
2. **Re-run the whole checklist of gate 1** (its requirement map R1–R10 → named running tests; the
   operator-visible guarantees; the phases 0–3 regression guarantees). Run the suite yourself:
   `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
3. **Hunt for defects introduced by the fix round** — an assertion weakened to match an implementation,
   a canonicalisation that now refuses a legitimate overlay, a bound that a healthy request can trip, a
   newly swallowed error, or a status code that misreports what happened.

## Deliverable (write to `docs/campaigns/phase4/scaffolding/gate-report-2.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound), on its own line, plainly
- **PREVIOUS FINDINGS**: F1–F5, each with status, `file:line`, the executed test, and the outcome of
  re-running its counterexample (including any attempt to defeat the fix)
- **CHECKS**: one line each, PASS/FAIL/NOT-VERIFIABLE with the command used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 4. If everything you can check passes, say APPROVE plainly.
