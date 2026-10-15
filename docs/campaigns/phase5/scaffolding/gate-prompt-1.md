# Phase 5 — full-branch closing gate (first pass)

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Phase 5 added an **optional bridge
to a stream-automation application** — a sink that runs a named action of the operator's instance, a
trigger input for events the instance routes to the companion, a provider that may *replace* the
basic inputs (chat, viewers), and the kernel's missing **input-ownership** rule (at most one provider
per (family, platform) pair, refused at preparation). Its integration study is
`docs/research/streamerbot-etude-2026-10.md`.

The phase 5 specification (`docs/campaigns/phase5/spec.md`, 10 requirements, 45 acceptance criteria,
approved after a hand verification of the pipeline's rounds) and plan (`docs/campaigns/phase5/plan.md`,
22 steps, with its deviations D1–D8) are the authority; the brief
(`docs/campaigns/phase5/brief.md`) carries the operator's binding decisions. **Read
`docs/campaigns/phase5/scaffolding/plan-amendments.md` as well**: it records deviation D9, the kernel's
string length bounds added by step P12F1, and the two corrections to step P12 (the `companion_name`
setting and three more assertions inside an already allowlisted test), each with the evidence and the
alternative that was refused.

**Write your report to `docs/campaigns/phase5/full-branch-review.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits,
no other file. Review the whole branch diff, not one commit at a time: cross-file integration is
where this kind of feature breaks.

## Your job

1. **Map every requirement to a named running test.** For each of R1–R10 in the specification, name
   the test that enforces it (`tests/<file>.py::<test>`) and say PASS/FAIL/NOT-VERIFIABLE with the
   command you used. Run the suite yourself:
   `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (the system python lacks `aiohttp`).
   Run the trial nodes too: `pytest tests/test_phase5_trials.py -q` (8 collected, 8 skipped) and the
   documentation-status test.
2. **Verify the operator-visible guarantees, with an executed counterexample for any failure:**
   - **Optionality is total**: the four shipped `*.example` profiles have zero changed lines; with the
     bridge absent, disabled, or enabled-but-unreachable, the companion is *ready* with an explicitly
     unavailable capability and no setting became required at startup; nothing in `core/` imports or
     names the bridge.
   - **One owner per (family, platform)**: try to make two providers ingest the same stream — a
     configuration naming two, two manifests both declaring `default: true`, a v1 module with no
     declaration, a wildcard owner against a concrete one. Each must be either refused before any
     module is prepared (naming family and both providers) or resolved by the documented rule; a
     publication by a non-owner must be dropped, counted and traced — never delivered to a subscriber.
   - **Honest silence**: try to make an unconfirmed execution read as a success. A pre-write failure,
     a cancellation before the frame leaves, a deadline while the hook is blocked, an acknowledgement
     lost after the send — each must map exactly as deviation D8 states, and no acknowledged run may
     report `ok`.
   - **The allowlist holds before the network**: a name outside it, a group entry, a name that is
     missing, disabled or ambiguous in the instance's inventory — each refused with **zero request
     frames** leaving the process. A principal without an `actions:` rule is refused even for an
     allowlisted name.
   - **No secret value escapes**: the instance's configured password appears in no trace, diagnostic,
     page or log — only its reference and its set/unset state.
   - **Not delivered**: while no trial record exists, the README and the documentation index carry
     "not delivered" for the bridge and the word "delivered" is applied to it nowhere else.
   - **Vendor-free kernel**: a grep of `core/` for the bridge module name and for the product/vendor
     name (any case) finds nothing.
   - **The aiohttp canary**: it must still pin the *installed* writer's behaviour, and fail loudly if
     that behaviour changed — a canary that passes vacuously is worse than none.
3. **Check the phases 0–4 guarantees still hold** (`tests/` overall): default-deny authorization
   including reads, bounded admission and retention, explicit terminal action outcomes, the single
   global startup and shutdown deadline, secret redaction, the phase 2 deadline ruling (R10) and
   no-default-provider rule, the phase 3 moderation modes with the strictest default and no permanent
   bans, viewer-memory bounds and analysis-based eviction, the requested-only watch input, honest
   platform capabilities, and the phase 4 configuration UI's local-only surface with `secrets` and
   `actions` read-only.
4. **Hunt for new defects** introduced by this phase: a guard a module can bypass by publishing
   directly, an ownership table the loader and the runtime disagree about, a gated binding that
   overlaps for a wildcard owner, an inbound queue bound that can stall the socket, a mapping that
   invents payload fields the native module does not produce, a documented claim the code does not
   honour, or an *adapted* allowlisted test that was in fact weakened — compare each adapted test
   against its entry in the specification's allowlist.

## Known-good anchors — do not report these

- The seven tests in the specification's `## Test failure allowlist` are expected to be adapted, with
  the reason stated; only a weakened assertion is a finding.
- `core/actions.py` and `core/triggers.py` are touched although the specification's cost row said five
  core files: deviations D5 and D6 of the plan approve both.
- `core/contracts.py` accepts two schema keywords (`minLength`, `maxLength`) that no requirement of
  this phase expected: deviation D9, recorded with its evidence in
  `docs/campaigns/phase5/scaffolding/plan-amendments.md`, is approved. The bridge's
  `companion_name` setting and the three extra assertions in an already allowlisted test are approved
  corrections of step P12, recorded in the same file.
- `core/runtime.py`'s health observation no longer lets a successful phase overwrite a state the module
  reported itself: deviation D10, added by step P14F1, is approved — without it an unreachable
  instance left the bridge ready while AC3's literal wording still passed. Verify the invariant, do not
  report the change.
- `core/actions.py`'s terminal records now carry a provider's `cause` on the paths the executor owns
  (conversion to `external_unknown`, and take-over on cancellation or deadline): deviation D11, added by
  step P15F1, is approved. It closes a latent gap in the shipped `clips` and `stream_control` modules
  too. Verify that no status or code changed, do not report the amendment.
- The confirmation round trip is absent on purpose (assumption A6): an acknowledged run ends
  `external_unknown execution_unconfirmed`.
- The `viewers` family merges "viewer reading" and "presence" (assumption A4): the repository has one
  such capability, and adding a second that replaces nothing was explicitly declined.
- The phase is not "delivered" at merge; the trial record is outside this plan.

## Deliverable (write to `docs/campaigns/phase5/full-branch-review.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE
- **GUARANTEES**: one line per guarantee above, with the command or counterexample used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 5. If everything you can check passes, say APPROVE plainly.
