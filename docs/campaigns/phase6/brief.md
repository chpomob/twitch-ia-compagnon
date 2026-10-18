# Brief — Phase 6: closing the defects of the full adversarial review

Project: `twitch-ia-compagnon` (repo `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`,
HEAD `29384cc`). Phases 0–5 are delivered on `main`; 3,677 tests green; the phase-5 closing gate
approved the tree at `d169150`.

## Problem

A full adversarial review of the whole product was run on `2026-10-16` by two independent models with
different lenses, followed by a mutual cross-review and a first-hand verification of every finding rated
major or above. Its report is the authority for this phase and must be read in full:

`docs/reviews/2026-10-16-full-adversarial-review.md`

It records **35 distinct defects** after deduplication (38 raw findings, 3 found independently by both
models), classed by the reviewers as 11 major, 17 minor and 7 nit, plus two findings *challenged* in
cross-review (see "Out of scope"). The reviewers' own verdict was `REQUEST_CHANGES` from both sides.

The pattern the review established is worth stating in the brief because it dictates the shape of the
fixes: the kernel's discipline survived scrutiny — bounded stores, single-owner inputs, honest outcomes,
value-free diagnostics — and the defects that matter live **on the boundaries between modules**: a
compatibility route that skips the executor, an agent that trusts a `principal` chosen by its peer, an
observation cache keyed by `call_id` alone, a settings diagnostic that echoes a key, an erasure that
stops at the module that owns the file. Every one of those is an integration defect, invisible to a
per-commit review. The phase must fix them as boundaries, not as spots.

## Decisions already made by the operator (binding; a specification that narrows them is wrong)

- **1 — All 35 findings are in scope, in the reviewer's priority order.** A specification that defers a
  security-boundary defect (report group 1) to a later phase is wrong. Grouping into steps is expected;
  dropping a finding is not.
- **2 — Each fix ships with the regression that would have caught it.** A fix without a test that fails
  on the pre-fix tree is incomplete. The review's own reproductions are the starting point: several are
  already executable counterexamples in the report.
- **3 — No fix may be obtained by weakening the suite.** Never skip, xfail, delete or broaden an
  assertion to make a red test pass. If a test encodes the defective behaviour, it is replaced and the
  finding id and the requirement it restores are cited in the docstring.
- **4 — Phase-5 decisions stay binding.** The bridge remains optional in every case; its events replace
  the basic inputs and never run beside them; an execution the instance cannot confirm is never reported
  as a success; authorization is DEFAULT-DENY; an input pair has exactly one owner.
- **5 — The compatibility route goes through the executor.** `docs/design-v2.md:584` states that output
  compatibility does not bypass the executor. That sentence is the contract the phase must make true;
  it is not a suggestion to be re-decided.
- **6 — The redaction class must be closed by construction, not by another surface patch.** Phase 5
  already spent three rounds on this class (ownership diagnostics → whole-diagnostic redaction → shared
  primitive) and still left a fifth surface open (settings-schema validation echoes the raw key). The
  finding is not "one more call site": the phase must make the class impossible — one primitive, every
  consumer enumerated *by a structural test*, so a sixth surface fails the suite instead of leaking a
  credential.
- **7 — A peer is not an identity.** `agent_link` currently takes the `principal` verbatim from the paired
  brain's frame and does not enforce the negotiated action set on receipt. The fix must establish what
  the remote peer is allowed to be, not merely validate the shape of what it sends.
- **8 — Nothing changes for a shipped profile beyond the finding being fixed.** No new required setting,
  no new dependency, no change to a default path, except where the finding *is* that default path (the
  installation guide's endpoint value, the README's command).
- **9 — The bridge stays "not delivered".** No real-instance trial exists; the phase must not claim
  otherwise, and the docs guard enforcing that status must keep passing.

## Scope, by the report's groups

**Group 1 — security and trust boundaries (fix first, no deferral):**
the Twitch compatibility route that bypasses default-deny (`modules/twitch/__init__.py:1521`);
`agent_link` executing actions outside the negotiated set and trusting a peer-chosen `principal`
(`modules/agent_link/__init__.py:1083`, `:1154`); the observation cache keyed by `call_id` alone,
returned before authorization (`core/actions.py:1197`); the settings diagnostic that echoes a credential
used as a key (`core/loader.py:1185`); peer-controlled text interpolated into the agent's diagnostics
(`modules/agent_link/__init__.py:990`); `!forgetme` leaving the brain's conversation memory intact
(`modules/brain/__init__.py:1855`).

**Group 2 — silent failures and integrity:**
concurrent duplicate `call_id` reaching a provider twice (`core/actions.py:1197`); a raising supervision
publication killing the bridge's owner task (`modules/streamerbot/__init__.py:2399`); Twitch retrying a
refused token forever without reporting `degraded` (`:2034`); no EventSub keepalive watchdog (`:2044`);
YouTube re-ingesting the first page after a restart (`modules/youtube/__init__.py:1388`); no response
byte bound before JSON decoding (`modules/brain/__init__.py:4547`); unauthenticated proxy connections
without admission or a hello deadline (`modules/proxy/__init__.py:982`); unbounded running executions
behind a bounded call cache (`modules/agent_link/__init__.py:1105`).

**Group 3 — first installation and documentation:**
the installation guide's `MODEL_ENDPOINT` value producing a non-working URL (`docs/installation.md:112`);
the README's configuration-UI command (`README.md:130`); the overlay filename in the install guide
(`docs/installation.md:353`); documentation tests that grep text instead of exercising the code
(`tests/test_installation_doc.py:117`).

**Group 4 — robustness and minors:** the remaining findings of the report's groups 3 and 4 (viewer-memory
eviction before a failed write, leaked temporary files, admission's stale ready entries, Twitch send
length, Kick dedup window, unknown-setting acceptance, unasserted brain budget, the vacuous bridge test,
`NaN` grace values, audio-output shutdown delay, the `$`-anchored scan, two tautological assertions, the
unwrapped retention copy). Fix them; group them into as few steps as their dependencies allow.

## Out of scope

- The two findings **challenged** in cross-review are not defects and must not be "fixed": the
  Streamer.bot backoff reset is documented behaviour (`modules/streamerbot/__init__.py:2328`), and the
  stalled-subscriber scenario (`codex:F6`) needs an out-of-tree subscriber.
- The limits the report states as unverified: no real platform, Streamer.bot instance, OBS, model
  service or audio device is contacted in this phase; compatibility remains a claim for the real trial.
- New features, refactors with no finding behind them, and style changes.

## What the phase must produce

The fixes, their regressions, and a closing gate as strict as phase 5's: a full-branch review by the
reviewer model, with its report tracked in the repository, and `APPROVE` required before the phase is
called done. The gate must re-run the review's own reproductions for the findings it claimed to close —
an assessment that something "looks fixed" is not evidence.

The phase summary must state, finding by finding, either the commit and test that closes it or the
reason it remains open. A phase that closes 30 of 35 and says so is worth more than one that claims 35.
