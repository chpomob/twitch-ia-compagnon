# Step P20F2 — one redacting constructor for hook diagnostics, structural test proving it by construction

Gate fix step of Phase 6. It answers the closing gate's **first pass**
(`docs/campaigns/phase6/full-branch-review.md`), which refused approval with 29 findings closed and five
security-boundary findings open.

## The finding (verbatim)

- **F4 OPEN** — loader/CLI-check/UI/supervision AC10 regressions pass; baseline **26 failed, 3 passed**. Offline CLI probe C still prints `field 'review-secret-4521'` to stderr for a credential used as a key. Structural mutation D also lets three equivalent unredacted surfaces pass AC11, so the class is not closed by construction.

### C — offline settings consumer still leaks a credential key (F4)

Executed `modules.viewer_memory.__main__.main(['stats', '--config', '/virtual/gate.yaml'])` with `Path.read_text` supplying this in-memory YAML document (no file/store access):

```yaml
modules:
  streamerbot:
    password: review-secret-4521
  viewer_memory:
    directory: /tmp/phase6-unused-memory
    review-secret-4521: 1
```

Observed **exit 2**, stderr:

```text
viewer_memory: module 'viewer_memory': field 'review-secret-4521': is not a setting this module declares
```

`_memory_directory` joins a module hook's raw diagnostics and `main` prints them. This caller does not pass through the loader's credential guard. A structural rule that treats hook reachability as sufficient misses this second reporter. The original `--check-config` reproduction is fixed; this sibling consumer keeps the redaction finding open.

### D — AC11 catches its chosen spelling, misses equivalent surfaces (F4; N1)

A virtual `core/_gate_virtual_fixture.py` was appended to the real scan. I called the actual `test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive`, without changing that test:

| Fixture body, inside `for key in settings` | Structural-test output |
|---|---|
| `raise ValueError(f'settings.{key}: unknown')` | **FAIL**, violation `('core/_gate_virtual_fixture.py', 'check'): [3]` |
| `raise ValueError('settings.{}: unknown'.format(key))` | **PASS**, violations `{}` |
| `raise ValueError('settings.' + key + ': unknown')` | **PASS**, violations `{}` |
| Same raw f-string, preceded by unrelated `safe = mask_known('fixed', known)` | **PASS**, violations `{}` |

The first is the required negative control and really fails. The others prove that the general structural contract is false. The current heuristic recognizes selected f-strings and considers any redactor call in a function sufficient, regardless of which diagnostic it sanitizes. Its docstring acknowledges these limits; acknowledgment does not satisfy the brief's boundary-wide requirement.

## Required resolution

Two defects in one class. (a) An offline consumer — `modules.viewer_memory.__main__`
(probe C) — prints a module hook's raw diagnostic, credential key included, because it joins the hook's
text itself; the loader's guard is not on that path. (b) AC11's structural test recognises selected
f-strings and treats any redactor call in a function as sufficient, so `.format`, concatenation, or an
unrelated redactor call pass while the surface stays raw (probe D).

Required: make the diagnostic a VALUE that cannot be rendered raw. The hook's text is produced by a
single constructor that redacts with the shared primitive and returns the final string; no reporter —
the loader, `--check-config`, the config UI, a `__main__` CLI, supervision, an exception message —
formats the hook's fields itself. If a hook raises, the constructor is the only way its reason becomes
text. Then the structural test proves the INVARIANT rather than a spelling: it fails when any module
renders a hook's field path, `reason`, or `str()` outside that constructor (probe D's four fixtures are
the negative controls, all four must fail), and the offline consumer of probe C prints nothing
unredacted.

## Files

[`core/loader.py`, `modules/viewer_memory/__main__.py`, `tests/test_redaction_surfaces.py`, `tests/test_loader.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing it.

## Tests

Probe C's exact case (a credential used as an unknown key, through
`modules.viewer_memory.__main__.main(['stats', ...])`) exits non-zero with the credential absent from
stdout and stderr. Probe D's four fixtures are all caught: raw f-string, `.format`, concatenation, and a
raw f-string preceded by an unrelated redactor call. Each is a negative control that must FAIL the
structural test. The positive control (a surface that builds its diagnostic through the constructor)
passes. The existing AC10/AC12/AC11 cases stay green.

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
