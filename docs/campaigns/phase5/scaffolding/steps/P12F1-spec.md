# Step P12F1 — the core schema subset accepts string length bounds (deviation D9)

Fix step created during the Phase 5 campaign. It is **not** one of the plan's numbered steps: it
exists because step P12 stopped before editing anything, exactly as its instructions require, and
reported a conflict between the approved plan and the kernel's real capabilities. Its report is
preserved at `docs/campaigns/phase5/scaffolding/loop-out/phase5-p12/01_build.json`.

Authority documents (read them, do not re-derive):
- Approved plan: `docs/campaigns/phase5/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase5/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`
- The record of this amendment and its rationale: `docs/campaigns/phase5/scaffolding/plan-amendments.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules).
Working tree at `main`.

## Problem

Step P12 must declare the bridge's run action with an argument schema that bounds the length of its
`action` string, because acceptance criterion **AC15** requires the *argument schema itself* to refuse
an `action` of 0 or 129 characters:

    {type: object,
     properties: {action: {type: string, minLength: 1, maxLength: 128}, arguments: {type: object}},
     required: [action], additionalProperties: false}

The kernel's schema subset does not support `minLength`/`maxLength`. In `core/contracts.py`:

- `_CONSTRAINT_KEYWORDS` is exactly `type, enum, minimum, maximum, required, properties, items,
  additionalProperties`, and `_KNOWN_KEYWORDS` adds only the annotations `title`, `description`,
  `default`. Any other keyword is **refused on purpose** — an unsupported constraint must never look
  enforced.
- `ActionSpec.__post_init__` validates `argument_schema` with `validate_schema`, so the loader rejects
  the manifest at declaration:

      SchemaError: argument_schema.properties.action.minLength: unsupported schema keyword;
      supported: additionalProperties, default, description, enum, items, maximum, minimum,
      properties, required, title, type

The rest of the specification's criteria are unaffected: no AC in this phase needs any other new
keyword, and no module in the tree declares a length bound today.

## Requirements

Plan mapping: R5, R10
Acceptance criteria owned by this step: none directly — this step exists so that **AC15** can be
satisfied as written. Do not touch AC15's text or the specification.

Note for the record: the alternative — moving the length check into the provider and rewording AC15 —
was **refused**, because the brief's binding decisions state that a specification which narrows them
is wrong, and AC15 is approved wording. The kernel grows the capability instead.

## Problem details to implement (the reporter's proposal, to be validated by your own reading)

In `core/contracts.py`, additively:

- add `minLength` and `maxLength` to `_CONSTRAINT_KEYWORDS`;
- in the schema validator, accept each only as a non-negative `int` (`bool` excluded, like the
  existing numeric bounds), and refuse a schema where `minLength > maxLength`;
- apply them to **strings only**, counting code points (`len(str)`), with diagnostics shaped like the
  existing bounds — the neighbouring table uses `("minimum", lambda v, b: v >= b, "must be >=")` and
  `("maximum", lambda v, b: v <= b, "must be <=")`, and the failure must name the offending field path
  the same way (`<path>.minLength: ...`);
- a node declaring `minLength`/`maxLength` where the constraint cannot be enforced — a node whose type
  is not a string — is **refused at validation**, consistent with the subset's existing stance that an
  unsupported constraint never looks enforced. State this rule in the code's own comment.
- no existing schema in the tree declares these keywords, so no existing validation outcome changes.

## Files

[`core/contracts.py`, `tests/test_contracts.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of
editing it.

## Dependencies

[P11]

All dependencies are already merged into `main` when this step runs.

## Tests

In `tests/test_contracts.py`, added to the existing schema-validation tests: a string node with
`minLength`/`maxLength` accepts a value inside the bounds and refuses one below `minLength` and one
above `maxLength`, each with a diagnostic naming the field and the bound; the same keywords on a
nested `properties`/`items` node are enforced; a `bool` bound, a negative bound and a
`minLength > maxLength` schema are each refused at validation (naming the field); a node whose type is
not a string declaring either keyword is refused at validation; and one test asserts that a schema
without these keywords validates exactly as before (the subset's other refusals are unchanged — an
unknown keyword is still refused).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,312 passed / 18 skipped, the count when this step starts.

## Risks

- Widening a deliberately closed vocabulary invites the next unsupported keyword. Keep the change
  minimal and keep the refusal of everything else intact: an unknown keyword must still fail.
- A length bound applied to the wrong type would silently pass an unenforced constraint — that is why
  a non-string node declaring it must be refused rather than ignored.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P12F1 — ...`). The scope MUST be `phase5`.
- Keep the phase-0 to phase-4 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, and redaction of configured secrets in traces and loss
  diagnostics.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages — `core/` stays vendor-free (AC11).
- Report at the end: what changed, the exact test command output count, and anything you could not do
  because this amendment did not cover it.
