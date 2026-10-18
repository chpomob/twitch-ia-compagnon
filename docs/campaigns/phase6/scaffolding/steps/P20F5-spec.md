# Step P20F5 — restore the F18 guarantee, and record the allowlist amendment the phase needs

Gate fix step of Phase 6. It answers the closing gate's **first pass**
(`docs/campaigns/phase6/full-branch-review.md`), which refused approval with 29 findings closed and five
security-boundary findings open.

## The finding (verbatim)

- **N2 (minor)** — the F18 replacement drops the guide's UI-profile argument guarantee: changing
  only the UI command to `--config wrong.yaml` passes the replacement while falsifying the old exact
  assertion.
- **N3 (minor)** — five existing tests outside the allowlist had their assertion ASTs changed:
  `test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant`;
  `test_proxy.py::test_agent_link_manifest_declares_the_input_role_no_event_and_no_action`,
  `::test_agent_link_validate_settings_accepts_loopback_ws_and_wss_anywhere`,
  `::test_agent_link_validate_settings_refuses_bad_entries_and_non_finite_bounds`;
  `test_youtube.py::test_ac39_three_platforms_on_one_runtime` (expected list count 1 → 2 after priming).
  The schema/validation tests extend exact expected sets; YouTube's count changes because F11 primes the
  first page. Sensible adaptations, but the binding allowlist does not authorise them.



## Required resolution

Two jobs.

(a) **Restore the guarantee N2 lost.** The F18 replacement must assert BOTH that the printed commands
resolve against the code AND that the guide's UI command still carries its profile argument
(`--config mon-compagnon.yaml`). Changing it to `wrong.yaml` must fail.

(b) **Record the amendment, do not widen the allowlist by stealth.** The five assertion changes are
consequences of this phase's own requirements: the manifests declare the new settings F2/F5/F14 added,
so an exact expected-set assertion must list them; and F11's first-page priming makes the YouTube
runtime admit one more message, so its count moves from 1 to 2. Write
`docs/campaigns/phase6/scaffolding/plan-amendments.md` — the record phase 5 used — with one entry per
test: what changed, why the phase's requirement forced it, and the evidence that it is an EXTENSION
(never a reduction): the old predicates still hold, the new ones are added, and no count was raised
without a behavioural reason. The gate's second pass will read this file; a silent change is what it
refused, not the change itself.

## Files

[`tests/test_installation_doc.py`, `tests/test_brain.py`, `tests/test_proxy.py`, `tests/test_youtube.py`, `docs/campaigns/phase6/scaffolding/plan-amendments.md`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing it.

## Tests

N2's counterexample: with the guide's UI command altered to `--config wrong.yaml`, the test
fails (both guides). The command-resolution checks stay green, and the README check stays green.
`plan-amendments.md` names the five tests, one entry each, and each entry says what extension happened;
the diff shows no removed assertion outside the specification's allowlist.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Never the system python: it lacks aiohttp. The suite must not drop below 3,849 passed / 26 skipped.

## Authority

- The closing gate's first pass, whose findings below are quoted verbatim with their executed probes:
  `docs/campaigns/phase6/full-branch-review.md` (authoritative for this step).
- The original review: `docs/reviews/2026-10-16-full-adversarial-review.md`.
- Brief, specification, plan: `docs/campaigns/phase6/brief.md`, `spec.md`, `plan.md`.
- The phase's amendment record: `docs/campaigns/phase6/scaffolding/plan-amendments.md`.
- The pre-fix tree is `8010edc`; the phase head is the current `main`.
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon`. Working tree at `main`.

## Standing phase-6 decisions

- Phase-5 decisions stay binding: optional bridge, replacement not parallel, honest outcomes,
  default-deny, single-owner inputs, the bridge "not delivered".
- Close exactly the finding(s) this step names. If a sibling path shows the same defect, report it in
  your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. Assertions may be replaced only for a test named in
  the specification's failure allowlist, with a docstring citing its finding and requirement; elsewhere
  an assertion may only be EXTENDED (a new check on top), never reduced.
- The full suite is green at every step: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit
  (`fix(phase6): P20Fn — ...`). The scope MUST be `phase6`.
- Never weaken, skip, xfail or delete an assertion. If an existing assertion encodes the defect, replace
  it only as the specification allows and cite finding + requirement in the docstring.
- No new runtime dependency; no vendor or model name in code or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do.
- Dependencies: P20F1..P20Fn land in order; each spec assumes the previous ones are on `main`.
