# Phase 4 — full-branch closing gate (first pass)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Phase 4 added a **local web
configuration UI** served by a separate process, whose pages are generated from each module's
manifest. The phase 4 specification (`docs/campaigns/phase4/spec.md`, approved after five
independent verification rounds) and plan (`docs/campaigns/phase4/plan.md`, 19 steps) are the
authority; the brief (`docs/campaigns/phase4/brief.md`) carries the operator's binding decisions,
including decision **6c** on the writing scope.

**Write your report to `docs/campaigns/phase4/scaffolding/gate-report-1.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file. Review the whole branch diff, not one commit at a time: cross-file integration is where this
kind of feature breaks.

## Your job

1. **Map every requirement to a named running test.** For each of R1–R10 in the specification, name the
   test that enforces it (`tests/<file>.py::<test>`) and say PASS/FAIL/NOT-VERIFIABLE with the command you
   used. Run the suite yourself:
   `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (the system python lacks `aiohttp`).
2. **Verify the operator-visible guarantees, with an executed counterexample for any failure:**
   - **Writing scope (6c)**: the UI writes exactly `enabled_modules`, `modules.<name>`, `triggers`,
     `limits`, `modules_directory`; it can never write `secrets` or `actions`; the `actions` block is
     displayed read-only with the reason. Prove that no request, route or helper can write those two
     blocks or widen the authorization set.
   - **No secret value escapes**: no value of a `secrets`-listed variable or of any manifest-declared
     credential appears in any page, JSON response, diagnostic, audit line or log — only the reference and
     the set/unset state. Try to make it leak.
   - **Overlay**: writes go to the managed overlay only; the base file is never rewritten; a write deep-merges
     with documented precedence; removing an override restores the base value; the runtime and
     `--check-config` honour the overlay identically; comments in the base file are preserved.
   - **Local-only surface**: loopback bind by default, refusal (named diagnostic, non-zero exit, before
     binding) for a non-loopback host without the explicit flag; token required; Host/Origin guard;
     CSRF protection on writes; only the managed overlay path is writable.
   - **One page per module, generated**: every one of the 17 modules gets a page from its manifest with
     labels and defaults, and no UI code names a module; no module ships HTML.
   - **Check**: validates a **draft** (unsaved) configuration through the existing check path, opens no
     socket, does not touch the running process, and reports every diagnostic it can rather than the first.
   - **Apply**: a supervised restart with a declared launch command, honest reporting of whether the new
     configuration was accepted, and of whether disk and the running process agree (including the
     "unknown" and "unusable record" outcomes).
   - **No new runtime dependency** beyond aiohttp and PyYAML, and the UI works fully offline (no CDN, no
     external asset, no network fetch at render time).
3. **Check the phases 0–3 guarantees still hold** (`tests/` overall): default-deny authorization including
   reads, bounded admission and retention, explicit terminal action outcomes, the single global startup and
   shutdown deadline, secret redaction, the phase 2 deadline ruling (R10), the phase 2 no-default-provider
   rule, the phase 3 moderation modes with the strictest default and no permanent bans, viewer-memory bounds
   and analysis-based eviction, the requested-only watch input, and honest platform capabilities.
4. **Hunt for new defects** introduced by this phase: an assert weakened to match an implementation, a route
   that trusts a draft value, an unbounded response or page, a growth that ignores the configured bounds, a
   page that renders a setting it cannot honour, a restart path that can lose the operator's unapplied edits,
   or any behaviour the UI shows that the runtime does not actually do.

## Deliverable (write to `docs/campaigns/phase4/scaffolding/gate-report-1.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE
- **GUARANTEES**: one line per guarantee above, with the command or counterexample used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 4. If everything you can check passes, say APPROVE plainly.
