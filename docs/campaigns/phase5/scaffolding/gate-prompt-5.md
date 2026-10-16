# Phase 5 — full-branch closing gate (fifth pass)

Pass 4 confirmed P22F6's redesign closes every N2 reproduction, kept F1–F3 resolved and F4's and N1's
cases corrected, and refused approval for one remaining reason: **N3** — two redactors that do not use
the loader's primitive still search an already modified string, and the phase's new ownership page and
ownership-rejection trace consume them. Its required closure was architectural, and the operator's
stop-patching rule was applied: step `P22F7` makes the union-of-original-matches protection one
contract shared by every consumer that emits ownership data, deletes the ordering-dependent loops, and
adds a structural guard against their return. Spec:
`docs/campaigns/phase5/scaffolding/steps/P22F7-spec.md`.

**Write your report to `docs/campaigns/phase5/full-branch-review-5.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else.

## First job: settle N3, then confirm nothing regressed

Re-run N3's reproduction (self-contained in the pass-4 report) and state RESOLVED / PARTIAL /
UNRESOLVED with the observed output — the rendered row's cell text and data attribute, and the trace
payload's keys and values, asserted against whole credentials and against surviving halves. Then
confirm N2's and N1's reproductions and F1–F4 are still resolved.

If a **different** consumer of this same overlapping-value class is still reachable, say so plainly as
the blocker: this is the third report on the mechanism and the fourth fix step, so a fourth instance
means the enumeration was incomplete, which is a finding about the enumeration rather than about the
consumers.


## The remaining blocker, quoted verbatim

### N3 — MEDIUM: the overlap redesign does not reach ownership pages or rejection traces

This is further evidence of the still-partial F4/N1/N2 confidentiality guarantee. P22F6 fixes `core/loader.py::_redact_values`; two independently used redactors still search an already modified string:

- `core/config_ui/__init__.py:1485`–`1490`, `_redact`, sorts raw/JSON/HTML forms longest first through `_redaction_forms` and repeatedly replaces them. The phase-5 `_ownership_section` at lines 3182–3258 renders raw resolved platform names through this function in both cell text and a data attribute. Resolver diagnostics are not involved in a successful table render.
- `core/contracts.py:2234`–`2238`, `_redact`, likewise replaces each value sequentially. `Supervision.emit` uses sanitize_trace, so `_InputGuard.reject` at `core/runtime.py:1366`–`1368` exposes the same suffix in the platform of `input.ownership.rejected`. The underlying chat publication is correctly refused; the disclosure is in its diagnostic trace.

Both complete known values are available. Removing `context-prefix-review-` first destroys the full `review-secret-4521` match and leaves `secret-4521`. Neither normalization nor truncation is necessary. These redaction loops predate P22F6; the new ownership outputs consume them, so this is a residual integration gap rather than a claim that the new loader algorithm fragments text.

Executed reproduction (synthetic credentials, no file writes, no instance connection; only parsed configuration reads are substituted). The first configuration has complete settings and **passes the real check_config with status 0**, removing any reliance on an invalid native configuration. The UI is exercised through its real authenticated request handler. The trace control uses the same real guard/runtime helper as the ownership regression tests and installs both known values through Supervision.redact, the loader's normal registration surface:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests .venv/bin/python - <<'PY'
import asyncio
import re
from pathlib import Path
from unittest.mock import patch
import core.main as app
import core.config_ui as ui_app
from test_config_ui import _get, _logged_in, _section
from test_main import _finite_limits
from test_input_ownership import _guarded_runtime, _intruder, _chat_event

secret = 'review-secret-4521'
other = 'context-prefix-review-'
key = 'context-prefix-' + secret
config = {
    'modules_directory': str(Path('modules').resolve()),
    'enabled_modules': ['streamerbot', 'twitch'],
    'modules': {
        'streamerbot': {
            'url': 'ws://127.0.0.1:9/', 'companion_name': 'Companion',
            'password': secret,
        },
        'twitch': {
            'client_id': 'review-client-id', 'client_secret': 'review-native-key',
            'access_token': other, 'broadcaster_id': 'channel-1',
            'bot_user_id': 'bot-1', 'companion_name': 'Companion',
        },
    },
    'limits': _finite_limits(), 'inputs': {'chat': {key: 'none'}},
}

async def main():
    lines = []
    with patch.object(app, 'read_base', return_value=config), \
         patch.object(app, 'read_overlay', return_value={}):
        status = await app.check_config('config.yaml.example', environ={},
                                        diagnostic_reporter=lines.append)
    print('check_config:', status, lines)
    assert status == 0
asyncio.run(main())

with patch.object(ui_app, 'read_base', return_value=config), \
     patch.object(ui_app, 'read_overlay', return_value={}):
    ui = ui_app.ConfigUI(ui_app.UISettings.from_argv(
        ['--config', 'config.yaml.example']), environ={})
    response = _get(ui, '/core', _logged_in(ui))
    section = _section(response.body.decode(), 'input-ownership')
    print('HTTP status:', response.status)
    print('both declared credentials known:', {secret, other} <= ui.secret_values)
    print(next(row for row in re.findall(r'<tr.*?</tr>', section)
               if 'secret-4521' in row))
    print('credential suffix leaked:', secret[len(secret)//2:] in section)
    assert response.status == 200 and {secret, other} <= ui.secret_values
    assert secret[len(secret)//2:] in section

async def trace_control():
    runtime, _, _ = _guarded_runtime()
    runtime.supervision.redact((secret, other))
    context = _intruder(runtime)
    seen = []
    async def observe(event):
        seen.append(event)
    runtime.bus.subscribe('**', observe)
    event = _chat_event(key)
    await context.bus.publish(event['type'], event['payload'], event['metadata'])
    print('trace:', [(e['type'], e['payload']) for e in seen])
    print('rejections:', runtime.supervision.snapshot()['input_ownership_rejections'])
    assert [e['type'] for e in seen] == ['input.ownership.rejected']
    assert secret[len(secret)//2:] in seen[0]['payload']['platform']
asyncio.run(trace_control())
PY
```

Observed:

```text
check_config: 0 []
HTTP status: 200
both declared credentials known: True
<tr data-input-family="chat" data-input-platform="[hidden]secret-4521"><td>chat</td><td>[hidden]secret-4521</td><td class="owner">none</td><td class="origin">explicit</td><td class="candidates"></td></tr>
credential suffix leaked: True
trace: [('input.ownership.rejected', {'module': 'intruder', 'family': 'chat', 'platform': '[REDACTED]secret-4521'})]
rejections: 1
```

The full configured password has 18 characters; its tested last half `cret-4521` is present inside the surviving `secret-4521` in both outputs. This is a credential fragment disclosure, not an observation that a marker or a fixed vocabulary word coincides with a credential.

Required closure is an architectural one: make the union-of-original-matches protection apply to the redaction consumers that actually emit ownership data, including UI serialization forms and trace keys/values, with one defined semantic contract rather than independent replacement-order assumptions. Preserve exact non-secret text, UI structural-token/opaque-field protections, rendering escapes, bounds and publication refusal semantics. Prove the page response and emitted trace above contain neither whole credentials nor surviving halves. Do not attempt another field-specific patch; the operator's stop-patching/redesign rule now applies to these remaining consumers. No code, permission, ownership-order or network change is needed to reproduce the defect.

No further concrete defect was established in the guard, canary, memory correction, accepted amendments or allowlisted adaptations.


## Second job: the whole agenda again

Then repeat the first pass's full agenda on the current branch — requirement map (R1–R10 to named
running tests), guarantees with an executed counterexample for any claimed failure, the phases 0–4
regression check, and the hunt for defects the seven fixes may have introduced (including a redaction
that is over-broad, a structural guard that passes vacuously, and any rendering or refusal change the
shared primitive caused).

The known-good anchors stand: the seven allowlisted adaptations, D5–D11 and the approved P12/P14/P15
amendments — verify their invariants, do not report the changes themselves.

## Deliverable (write to `docs/campaigns/phase5/full-branch-review-5.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **PREVIOUS FINDINGS**: F1–F4, N1, N2, N3 — each RESOLVED/PARTIAL/UNRESOLVED with the observed output
- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE
- **GUARANTEES**: one line per guarantee, with the command or counterexample used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 5. If everything you can check passes, say APPROVE plainly.
