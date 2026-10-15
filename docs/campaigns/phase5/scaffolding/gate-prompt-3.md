# Phase 5 — full-branch closing gate (third pass)

Pass 1 refused approval with F1–F4; pass 2 settled F1, F2 and F3 as RESOLVED and left F4 PARTIAL, with
the remaining blocker recorded as **N1** in `docs/campaigns/phase5/full-branch-review-2.md`. One fix
step landed since: `P22F5` (every ownership diagnostic is redacted as a whole, not field by field),
whose spec is `docs/campaigns/phase5/scaffolding/steps/P22F5-spec.md`.

**Write your report to `docs/campaigns/phase5/full-branch-review-3.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else.

## First job: settle N1, with observed output

Re-run both of N1's reproductions (they are executable and self-contained in the pass-2 report)
against the current branch and state RESOLVED / PARTIAL / UNRESOLVED with the observed output. Then
confirm the first pass's four findings are still resolved — in particular that F4's two original cases
did not regress.

## The remaining blocker, quoted verbatim

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


## Second job: the whole agenda again

Then repeat the first pass's agenda in full on the current branch — the requirement map (R1–R10 to
named running tests), the guarantees with an executed counterexample for any claimed failure, the
phases 0–4 regression check, and the hunt for defects the five fixes may have introduced (a guard that
refuses legitimate publication, a canary that passes vacuously, a memory exclusion that also suppressed
ordinary recording, a redaction that is partial, over-broad or that fragments a credential).

The known-good anchors stand: the seven allowlisted adaptations, deviations D5–D11 and the approved
P12/P14/P15 amendments — verify their invariants, do not report the changes themselves.

## Deliverable (write to `docs/campaigns/phase5/full-branch-review-3.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **PREVIOUS FINDINGS**: F1–F4 and N1, each RESOLVED/PARTIAL/UNRESOLVED with the observed output
- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE
- **GUARANTEES**: one line per guarantee, with the command or counterexample used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 5. If everything you can check passes, say APPROVE plainly.
