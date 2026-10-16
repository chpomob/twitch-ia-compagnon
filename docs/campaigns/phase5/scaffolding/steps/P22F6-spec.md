# Step P22F6 — REDESIGN: the redaction masks the union of every credential match

Gate fix step of Phase 5, and a **redesign**, not a fourth surface patch. The closing review has now
reported the same mechanism three times: F4 (a diagnostic echoed a credential used as a key), N1 (the
redaction covered the fields a diagnostic is made of, not the diagnostic), N2 (the redaction itself
manufactures a fragment). The third report states the class precisely and prescribes the shape:

> The helper sorts values longest first, then repeatedly calls `str.replace` on the already modified
> string. A longer known value overlapping the beginning of a shorter credential is removed first. That
> destroys the shorter credential's full match and leaves its suffix intact. No normalization or
> truncation is needed: redaction itself creates the fragment.

> Required closure: determine all credential-match spans against the original dynamic text and mask
> their union, including overlapping matches, before normalization/bounding. **Merely changing
> replacement order or using a longest-match-only substitution does not establish complete protection.**

## Authority

- Third pass report — the finding below is quoted verbatim, with its reproductions:
  `docs/campaigns/phase5/full-branch-review-3.md`.
- The two earlier reports and the fix steps that led here: `docs/campaigns/phase5/full-branch-review.md`,
  `full-branch-review-2.md`, `scaffolding/steps/P22F4-spec.md`, `P22F5-spec.md`.
- Plan and specification: `docs/campaigns/phase5/plan.md`, `spec.md` (R8, AC23);
  `scaffolding/plan-amendments.md` (D1–D11).
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon`. Working tree at `main`.

## The finding (verbatim)

### N2 — MEDIUM: overlapping known values fragment a credential during redaction

This is further evidence of the unresolved complete-protection part of F4/N1. Locations: `core/loader.py:2439`–`2444` (`_redact_known`) delegates to `core/loader.py:2280`–`2284` (`_redact_values`); both P22F5 boundaries consume that helper, including `core/main.py`'s `_InputsShapeError.diagnostic` and the resolver formatter.

The helper sorts values longest first, then repeatedly calls str.replace on the already modified string. A longer known value overlapping the beginning of a shorter credential is removed first. That destroys the shorter credential's full match and leaves its suffix intact. No normalization or truncation is needed: redaction itself creates the fragment. In the reproduction, the original key contains the complete configured password `review-secret-4521`; after redaction it contains `secret-4521`, including the entire last half checked by the new regression convention. Both complete known values are available before rendering.

Executed reproduction (synthetic values, no file writes or instance connection; only parsed-document reads are substituted):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
import asyncio
from pathlib import Path
from unittest.mock import patch
import core.main as app
from test_main import _finite_limits
from test_input_ownership import _decl, _families
from core.loader import resolve_input_ownership, ModuleLoadError

secret = 'review-secret-4521'
other = 'context-prefix-review-'
key = 'context-prefix-' + secret

async def main():
    for inputs in ({key: 'not-a-mapping'}, {'chat': {key: 42}}):
        config = {
            'modules_directory': str(Path('modules').resolve()),
            'enabled_modules': ['streamerbot'],
            'modules': {'streamerbot': {
                'url': 'ws://127.0.0.1:9/', 'companion_name': 'Companion',
                'password': secret,
            }},
            'limits': _finite_limits(), 'inputs': inputs,
        }
        with patch.object(app, 'read_base', return_value=config), \
             patch.object(app, 'read_overlay', return_value={}), \
             patch.object(app, '_assemble_runtime') as assemble:
            for route in ('run', 'check_config'):
                lines = []
                if route == 'run':
                    status = await app.run('config.yaml.example', asyncio.Event(),
                        environ={'OTHER_PASSWORD': other}, diagnostic_reporter=lines.append)
                else:
                    status = await app.check_config('config.yaml.example',
                        environ={'OTHER_PASSWORD': other}, diagnostic_reporter=lines.append)
                print(route, status, lines)
                assert status == 2 and secret[len(secret)//2:] in '\n'.join(lines)
            print('runtime assemblies:', assemble.call_count)
            assert assemble.call_count == 0

asyncio.run(main())
declarations = {
    name: _decl(_families(('chat', [key], True)), name) for name in ('a', 'b')
}
try:
    resolve_input_ownership(['a', 'b'], declarations, {},
                            redacted=frozenset({secret, other}))
except ModuleLoadError as error:
    print(str(error))
    print('credential suffix leaked:', secret[len(secret)//2:] in str(error))
    assert secret[len(secret)//2:] in str(error)
else:
    raise AssertionError('expected refusal')
PY
```

Observed:

```text
run 2 ['inputs.<redacted>secret-4521: must be a mapping of platform to owner']
check_config 2 ['inputs.<redacted>secret-4521: must be a mapping of platform to owner']
runtime assemblies: 0
run 2 ['inputs.chat.<redacted>secret-4521: owner must be a module name or a list of module names']
check_config 2 ['inputs.chat.<redacted>secret-4521: owner must be a module name or a list of module names']
runtime assemblies: 0
inputs: family 'chat': platform '<redacted>secret-4521': owned by default by both 'a' and 'b'; set inputs.chat.<redacted>secret-4521
credential suffix leaked: True
```

An additional executed control removes any dependence on an unrelated environment value: enabled modules `['streamerbot', 'twitch']`, the same bridge password, `modules.twitch.access_token = other`, `inputs = {key: 'not-a-mapping'}`, and `environ={}`. Both strings are now literals at real manifest-declared credential paths. The same parsed-read substitutions and assembly spy were used. Observed:

```text
two declared credentials: run 2 ['inputs.<redacted>secret-4521: must be a mapping of platform to owner']
two declared credentials: check_config 2 ['inputs.<redacted>secret-4521: must be a mapping of platform to owner']
runtime assemblies: 0
```

This second control concerns early structural refusal; it does not claim the abbreviated native-module settings would pass later settings validation. Neither configuration reaches that validation or runtime construction.

Required closure: determine all credential-match spans against the original dynamic text and mask their union, including overlapping matches, before normalization/bounding. Merely changing replacement order or using a longest-match-only substitution does not establish complete protection. Keep fixed vocabulary and existing exact-text non-secret controls. Add overlapping-value regressions through both public entry points and the conflict's full reason/path, asserting the absence of whole credentials and surviving halves across the joined diagnostics. No ownership-order, runtime-permission or network change is required.

No further concrete defect was established in the guard, canary, memory correction, accepted amendments or allowlisted adaptations.

## Required resolution

**Replace the primitive's algorithm; do not add another guard around it.**

`core/loader.py` holds the single redaction primitive (`_redact_values`, reached through
`_redact_known`), and both P22F5 boundaries — the resolver's formatter and `core/main.py`'s
`_InputsShapeError.diagnostic` — already consume it. Fixing that one primitive is what makes this a
redesign of the class rather than a fourth patch of a spot.

Requirements:

- **All spans against the original text.** Determine every occurrence of every known credential in the
  **original** dynamic text — including occurrences that overlap each other or a longer value — and mask
  the **union** of those spans in one pass. The current loop replaces on an already-mutated string,
  which is exactly how `context-prefix-review-` + `review-secret-4521` becomes a surviving `secret-4521`.
- **No reliance on ordering.** Sorting longest first, or keeping only the longest match, must not be the
  mechanism: the report states that neither establishes complete protection.
- **The whole text, not the fields.** The primitive is called on complete diagnostic strings; keep it
  that way, so a future diagnostic inherits the protection without being enumerated.
- **Fixed vocabulary survives untouched.** A value that is not a configured credential must read
  exactly as today, so redaction stays useful (and the existing exact-text controls keep passing).
- **Empty and pathological inputs are safe.** An empty credential set, an empty string value, a value
  equal to `_REDACTED_VALUE`, and two identical values must not crash, loop or corrupt the text.
- **State the class closure in your report**: one sentence saying the primitive no longer creates
  fragments by construction, and what replaces the ordering assumption.

## Files

[`core/loader.py`, `tests/test_input_ownership.py`, `tests/test_main.py`]

Touch ONLY these files. `core/main.py` consumes the same primitive and needs no change; if you find that
it does, stop and report it rather than editing it.

## Tests

In `tests/test_input_ownership.py` and `tests/test_main.py`:

- **N2's reproduction, verbatim**: `secret = 'review-secret-4521'`, `other = 'context-prefix-review-'`,
  `key = 'context-prefix-' + secret`, through both `run` and `check_config` and through the resolver's
  conflict path — asserted on the **joined** diagnostics: neither whole credential nor either half of
  the secret survives, the refusal is preserved, `status == 2`, zero runtime assemblies;
- a matrix of overlap shapes against the primitive: one value nested inside another, two values with a
  shared prefix, a suffix, identical values, three-way overlap, a value repeated after masking, a value
  equal to the replacement marker, an empty value;
- the **whole-value and fragment** assertions are on the joined text, not per field;
- the non-secret controls keep their **exact full text**, proving the redaction did not become
  over-broad.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,637 passed / 26 skipped, the count this gate ran.

## Dependencies

[P22F5] — the previous gate fix lands first; all its dependencies are already merged into `main`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P22F6 — ...`). The scope MUST be `phase5`.
- Keep every phase-0 to phase-4 guarantee in force, and every phase-5 decision: the bridge stays
  optional in every case, its events may replace the basic inputs but never run in parallel with them,
  and an execution the instance cannot confirm is never reported as a success.
- Never weaken, skip, xfail or delete an existing assertion to make the suite pass. If an existing test
  encodes the fragmenting behaviour, replace it and cite N2 and R8 in its docstring.
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do.
