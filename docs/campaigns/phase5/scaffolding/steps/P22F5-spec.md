# Step P22F5 — every ownership diagnostic is redacted as a whole, not field by field

Gate fix step of Phase 5. It answers the remaining blocker of the **second** pass:
`docs/campaigns/phase5/full-branch-review-2.md`, where F1, F2 and F3 are RESOLVED and F4 is PARTIAL
because its required complete protection fails on two further executed paths.

## Authority

- The second pass's report — the finding below is quoted from it verbatim, with both reproductions:
  `docs/campaigns/phase5/full-branch-review-2.md`.
- The first pass's report and the earlier fix steps: `docs/campaigns/phase5/full-branch-review.md`,
  `docs/campaigns/phase5/scaffolding/steps/P22F4-spec.md`.
- Approved plan and specification: `docs/campaigns/phase5/plan.md`, `docs/campaigns/phase5/spec.md`
  (R8's redaction guarantee; AC23's trace sweep).
- Amendments: `docs/campaigns/phase5/scaffolding/plan-amendments.md`.
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon`. Working tree at `main`.

## The finding (verbatim)

### N1 — MEDIUM: F4's redaction remains incomplete outside the corrected quoted fields

This is further evidence of the unresolved part of F4, not a new objection to the approved early ownership-resolution order. Both reproductions use a synthetic credential and no persistent memory/configuration writes.

**A. Structural input diagnostics bypass the resolver entirely.** Locations: `core/main.py:1578`–`1583` and `core/main.py:1588`–`1590`. `_validate_inputs` interpolates raw family/platform keys into ConfigurationError paths. Both `run` and `check_config` report those errors verbatim before the corrected resolver is reached. A configured password copied into either malformed key is exposed in full.

Executed command (only parsed-document reads are substituted; loading, validation and both public entry points remain real; the assembly spy proves failure precedes runtime construction):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
import asyncio
from pathlib import Path
from unittest.mock import patch
import core.main as app
from test_main import _finite_limits
secret = 'review-secret-4521'
async def main():
    for inputs in ({secret: 'not-a-mapping'}, {'chat': {secret: 42}}):
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
                diagnostics = []
                if route == 'run':
                    status = await app.run('config.yaml.example', asyncio.Event(),
                        environ={}, diagnostic_reporter=diagnostics.append)
                else:
                    status = await app.check_config('config.yaml.example',
                        environ={}, diagnostic_reporter=diagnostics.append)
                print(route, status, diagnostics)
                assert status == 2 and any(secret in line for line in diagnostics)
            print('runtime assemblies:', assemble.call_count)
            assert assemble.call_count == 0
asyncio.run(main())
PY
```

Observed:

```text
run 2 ['inputs.review-secret-4521: must be a mapping of platform to owner']
check_config 2 ['inputs.review-secret-4521: must be a mapping of platform to owner']
runtime assemblies: 0
run 2 ['inputs.chat.review-secret-4521: owner must be a module name or a list of module names']
check_config 2 ['inputs.chat.review-secret-4521: owner must be a module name or a list of module names']
runtime assemblies: 0
```

**B. Default-owner conflicts redact the platform label but repeat the raw platform in the correction path.** Locations: `core/loader.py:2656`–`2658`, with `_inputs_line` at `core/loader.py:2457`. The known-credential set is available, but the reason contains `set inputs.{family}.{platform}` and raw owner reprs; `_inputs_line` only redacts its location fields. Its reason passes through `_value_free_reason`, which bounds text but does not redact known credentials. Generic platform names are valid manifest declarations, so this is a supported resolver input, not an invalid forged object.

Executed:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
from test_input_ownership import _decl, _families
from core.loader import resolve_input_ownership, ModuleLoadError
secret = 'review-secret-4521'
declarations = {
    name: _decl(_families(('chat', [secret], True)), name)
    for name in ('a', 'b')
}
try:
    resolve_input_ownership(['a', 'b'], declarations, {},
                            redacted=frozenset({secret}))
except ModuleLoadError as error:
    print(str(error))
    print('credential leaked:', secret in str(error))
    assert secret in str(error)
PY
```

Observed:

```text
inputs: family 'chat': platform '<redacted>': owned by default by both 'a' and 'b'; set inputs.chat.review-secret-4521
credential leaked: True
```

Required closure: protect structural input diagnostics as well as every dynamic component of resolver reasons before any normalization/truncation can leave a credential fragment. Preserve the refusal, field identification, provider conflict information and useful fixed vocabulary. Keep the existing exact-text controls for non-secret diagnostics, and add regressions for A through run/Check and for B's reason/path. Redacting only the location fields is insufficient. No runtime permission or network change is needed to reproduce either failure.

No further concrete defect was established in the guard, canary or memory corrections.

## Required resolution

The defect is a **shape** defect, not two unrelated spots: redaction was applied to the fields a
diagnostic is *made of* (a location's family, a platform label) while the *reason* interpolates raw
keys, and one route composes its path before the resolver is even reached. Patching the named spots
alone would leave the next diagnostic to reopen it.

Implement redaction as one boundary rule:

- **One redaction, on the whole diagnostic text**, using the same known-credential set the rest of the
  loader uses, applied at the point where a diagnostic leaves toward the operator — the resolver's
  refusal message *and* the structural validation that precedes it — before any normalisation,
  truncation or bounding can split a credential into a surviving fragment.
- **Both routes the operator sees must be covered**: startup `run` and `--check-config`, since N1-A
  shows both report the raw key in the error path with zero runtime assemblies.
- **Every dynamic component** is covered: interpolated paths, reason text, owner representations and
  any correction the diagnostic suggests (`set inputs.{family}.{platform}` is exactly where a raw
  key survives today).
- **Do not lose the diagnostic's value**: the offending field, the family vocabulary, which providers
  conflict, and the actionable correction all stay identifiable; only credential values disappear.
- **Fixed vocabulary is never redacted by accident**: text that is not a configured credential must
  read exactly as it does today, including its full original wording (the existing exact-text controls
  are the guard against over-redaction).
- If the credential set is not yet available at one of those boundaries, say so and stop rather than
  shipping a partially protected diagnostic.

## Files

[`core/main.py`, `core/loader.py`, `tests/test_input_ownership.py`, `tests/test_main.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing
it.

## Tests

In `tests/test_input_ownership.py` and `tests/test_main.py`:

- N1-A's two cases through **both** public entry points (`run` and `check_config`): the refusal is
  preserved, `status == 2`, the runtime is never assembled, and neither the secret nor any fragment of
  it appears in the diagnostics — assert on the whole reported text, not on one field;
- N1-B: the default-owner conflict whose platform is a configured credential — the refusal and the
  provider conflict are preserved, the correction path is present, and the credential appears nowhere
  in the message;
- the same two shapes with a **non-secret** key assert the **full exact text** of the diagnostic, so
  the redaction is proven not to be over-broad;
- a fragment check: assert that no quoted substring of the credential (its first half, its last half,
  the whole value) survives anywhere in the joined diagnostics.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,628 passed / 26 skipped, the count this gate ran.

## Dependencies

[P22F4] — the previous gate fix lands first; all its dependencies are already merged into `main`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P22F5 — ...`). The scope MUST be `phase5`.
- Keep every phase-0 to phase-4 guarantee in force, and every phase-5 decision: the bridge stays
  optional in every case, its events may replace the basic inputs but never run in parallel with them,
  and an execution the instance cannot confirm is never reported as a success.
- Never weaken, skip, xfail or delete an existing assertion to make the suite pass. If an existing test
  encodes the leaked diagnostic as expected text, replace it and cite N1 and R8 in its docstring.
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do.
