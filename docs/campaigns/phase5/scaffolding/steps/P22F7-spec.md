# Step P22F7 — REDESIGN: one protection contract, every consumer that emits ownership data

Gate fix step of Phase 5, and the second **redesign** the closing review has demanded. P22F6 replaced the
loader's redaction algorithm with the union of all credential match spans against the original text, and
the fourth report confirms every N2 reproduction is closed at the loader, startup and resolver
boundaries. It refuses approval for one reason: two redactors that do not go through that primitive
still search an already modified string, and the phase's own new outputs consume them.

The report names the shape and the remedy:

> Both complete known values are available. Removing `context-prefix-review-` first destroys the full
> `review-secret-4521` match and leaves `secret-4521`. Neither normalization nor truncation is
> necessary. These redaction loops predate P22F6; the new ownership outputs consume them, so this is a
> residual integration gap.

> Required closure is an architectural one: make the union-of-original-matches protection apply to the
> redaction consumers that actually emit ownership data, including UI serialization forms and trace
> keys/values, **with one defined semantic contract** rather than independent replacement-order
> assumptions. […] Do not attempt another field-specific patch; the operator's stop-patching/redesign
> rule now applies to these remaining consumers.

## Authority

- Fourth pass report — the finding below is quoted verbatim, with its executed reproduction:
  `docs/campaigns/phase5/full-branch-review-4.md`.
- The three earlier reports: `docs/campaigns/phase5/full-branch-review.md`,
  `full-branch-review-2.md`, `full-branch-review-3.md`.
- The fix steps that led here: `scaffolding/steps/P22F4-spec.md` … `P22F6-spec.md`.
- Plan and specification: `docs/campaigns/phase5/plan.md`, `spec.md` (R8, AC23);
  `scaffolding/plan-amendments.md` (D1–D11).
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon`. Working tree at `main`.

## The finding (verbatim)

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

## Required resolution

**One semantic contract, three consumers, no fourth copy.** The class is now fully enumerated; the
step's job is to make it disappear by construction rather than to patch its third instance.

1. **Define the contract once.** A single exported primitive owns the meaning "mask every known
   credential in this text, in every serialization form the caller emits, before the text is bounded,
   escaped or truncated". State that contract in one sentence in the primitive's docstring and name it
   in your report. It generalizes the loader's P22F6 algorithm: all match spans are located against the
   **original** text — for every form of every value — and the **union** of those spans is masked in one
   pass. Overlaps between values, between forms, and between a value and its own escaped form are all
   part of the union.
2. **Make the three consumers use it.** `core/loader.py`'s primitive stays the implementation;
   `core/contracts.py::_redact` (reached by `sanitize_trace`, hence by
   `input.ownership.rejected`) and `core/config_ui/__init__.py::_redact` (raw/JSON/HTML forms via
   `_redaction_forms`, reached by `_ownership_section`'s cell text and data attributes) delegate to it.
   Their replacement loops are deleted, not reordered: ordering must no longer be part of any
   correctness argument anywhere in the tree.
3. **Keep every protection these surfaces already have.** Exact non-secret text; the UI's
   structural-token and opaque-field protections; rendering escapes; the bounds that keep diagnostics
   short; and the publication refusal semantics of `_InputGuard.reject`. Redaction must not become a
   licence to change what is refused, published or rendered — only what is legible.
4. **Make recurrence impossible, not just fixed.** Add the structural guard: a test that fails if any
   module defines its own sequential known-value replacement instead of calling the shared primitive
   (the repo already uses this style for the vendor-name and no-xfail guards). Name the three consumers
   it covers.
5. **Prove N3 is closed**: the page response and the emitted trace from the report's reproduction
   contain neither whole credentials nor surviving halves — asserted on the joined text, with the
   fragment assertion (whole value, first half, last half) applied to the page body and to the trace
   payload, and the non-secret controls asserting their exact full text so over-redaction is excluded.

## Files

[`core/loader.py`, `core/contracts.py`, `core/config_ui/__init__.py`, `tests/test_input_ownership.py`,
`tests/test_contracts.py`, `tests/test_config_ui.py`, `tests/test_main.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing
it.

## Tests

- N3's reproduction, verbatim and end-to-end: the configuration with complete accepted settings passes
  the real `check_config` with status 0; the authenticated UI request returns 200 with **no** whole
  credential and **no** surviving half in the rendered row (cell text and data attribute); the
  ownership-rejection trace carries no credential fragment in any key or value; the chat publication is
  still refused.
- The shared primitive's unit matrix from P22F6 still passes, extended with the form dimension:
  a value that appears raw, JSON-escaped and HTML-escaped in the same text; a value that overlaps
  another value's escaped form; identical values in two forms.
- The structural guard of point 4, plus a negative control proving that guard fails on a module that
  reintroduces its own loop.
- The non-secret exact-text controls stay unchanged and green.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,659 passed / 26 skipped, the count this gate ran.

## Dependencies

[P22F6] — the previous gate fix lands first; all its dependencies are already merged into `main`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P22F7 — ...`). The scope MUST be `phase5`.
- Keep every phase-0 to phase-4 guarantee in force, and every phase-5 decision: the bridge stays
  optional in every case, its events may replace the basic inputs but never run in parallel with them,
  and an execution the instance cannot confirm is never reported as a success.
- Never weaken, skip, xfail or delete an existing assertion to make the suite pass. If an existing test
  encodes a fragmenting or ordering-dependent behaviour, replace it and cite N3 and R8 in its docstring.
- No new runtime dependency; no model, provider or vendor name in code, config or commit messages.
- Report at the end: the one-sentence contract, the three consumers, the exact test command output
  count, and anything you could not do.
