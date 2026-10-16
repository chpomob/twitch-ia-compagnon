# Phase 5 — full-branch closing gate (fourth pass)

Pass 1 refused with F1–F4; pass 2 settled F1–F3 and left F4 PARTIAL as N1; pass 3 settled N1, confirmed
F1–F3 and F4's original cases, and left the same mechanism PARTIAL as **N2** (the redaction primitive
manufactures a credential fragment when known values overlap). One fix step landed since: `P22F6`, a
**redesign** of the primitive — all match spans against the original text, union masked in one pass —
whose spec is `docs/campaigns/phase5/scaffolding/steps/P22F6-spec.md`.

**Write your report to `docs/campaigns/phase5/full-branch-review-4.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else.

## First job: settle N2 and confirm the rest

Re-run N2's reproductions (self-contained in the pass-3 report) and state RESOLVED / PARTIAL /
UNRESOLVED with the observed output. Then confirm N1's reproductions, F4's original cases and F1–F3 are
still resolved. If you find that the redesign left a credential or a fragment of one visible anywhere,
say so plainly with the executed counterexample — this is the third report on this mechanism, and the
operator's rule is to stop patching and redesign rather than iterate a fourth time.


## The remaining blocker, quoted verbatim

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


## Second job: the whole agenda again

Then repeat the first pass's full agenda on the current branch — requirement map (R1–R10 to named
running tests), guarantees with an executed counterexample for any claimed failure, the phases 0–4
regression check, and the hunt for defects the six fixes may have introduced.

The known-good anchors stand: the seven allowlisted adaptations, D5–D11 and the approved P12/P14/P15
amendments — verify their invariants, do not report the changes themselves.

## Deliverable (write to `docs/campaigns/phase5/full-branch-review-4.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **PREVIOUS FINDINGS**: F1–F4, N1, N2 — each RESOLVED/PARTIAL/UNRESOLVED with the observed output
- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE
- **GUARANTEES**: one line per guarantee, with the command or counterexample used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 5. If everything you can check passes, say APPROVE plainly.
