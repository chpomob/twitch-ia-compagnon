# Step P22F4 — ownership diagnostics never echo a configured credential

Gate fix step of Phase 5. It is **not** one of the plan's numbered steps: it answers finding **F4**
of the phase's closing review (`docs/campaigns/phase5/full-branch-review.md`), which refused approval of
the assembled branch.

### F4 — MEDIUM: ownership diagnostics echo a configured credential used as a key

Locations: `core/loader.py:2444` (_inputs_line), `core/loader.py:2501` onward (invalid family/platform diagnostics).

Ownership resolution deliberately runs before credential registration/redaction. It receives a redacted set and protects owner values, but family/platform keys are quoted without that protection. A value copied from the configured password into either key leaks into the resulting ModuleLoadError. These diagnostics are the ones startup/Check report. This is a new exception to the existing redaction guarantee.

Executed with a synthetic configured credential, no socket or activation:

~~~python
import asyncio
from pathlib import Path
from core.loader import ModuleLoader, ModuleLoadError
from conftest import runtime_context

async def main():
    secret = "review-secret-4521"
    for inputs in (
        {secret: {"x": "streamerbot"}},
        {"chat": {secret: "not-enabled"}},
    ):
        runtime = runtime_context()
        loader = ModuleLoader(
            runtime.bus, Path("modules"), context=runtime, environ={})
        try:
            await loader.activate_enabled({
                "enabled_modules": ["streamerbot"],
                "modules": {"streamerbot": {
                    "url": "ws://127.0.0.1:9/",
                    "companion_name": "Companion", "password": secret,
                }},
                "inputs": inputs,
            })
        except ModuleLoadError as error:
            print(str(error))
            assert secret in str(error)
            assert loader.activations == []
        else:
            raise AssertionError("expected refusal")

asyncio.run(main())
~~~

Observed:

~~~text
inputs: family 'review-secret-4521': platform 'x': value 'streamerbot': family is outside the vocabulary (chat, viewers, cues)
inputs: family 'chat': platform 'review-secret-4521': owner 'not-enabled' is not enabled
~~~

Both expose the complete configured credential with zero modules activated. Redact complete ownership diagnostics using the known credential set, including keys and embedded values, before returning them from the resolver; retain value-free field identification and test the startup/Check routes.

## Authority

Read these, do not re-derive them:
- The closing gate's report — the finding above is quoted from it verbatim, with its executed
  reproduction: `docs/campaigns/phase5/full-branch-review.md`.
- Approved plan: `docs/campaigns/phase5/plan.md` (deviations D1–D11 as recorded in
  `docs/campaigns/phase5/scaffolding/plan-amendments.md`).
- Approved specification: `docs/campaigns/phase5/spec.md`.
- Brief with the operator's binding decisions: `docs/campaigns/phase5/brief.md`.
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules).
Working tree at `main`.

The gate's finding is the specification of the defect; the required resolution below is the behaviour
to implement. Do not re-open the finding's premises and do not widen the change beyond it.

## Required resolution


Ownership resolution runs before credential registration and redaction, so the diagnostics it produces
are not protected. Family and platform keys are quoted verbatim, and a value copied from a configured
credential into either key is echoed complete — by startup and by `--check-config` alike.

Requirements:
- redact every ownership diagnostic with the same known-credential set the rest of the loader uses,
  covering **keys and embedded values**, not only owner values;
- keep the diagnostics useful without the secret: the offending field, the family vocabulary, the
  rejected owner and the refusal reason stay identifiable;
- the same redaction applies on both routes the operator sees: startup (`run`) and `--check-config`, and
  on the configuration UI's Check path if it renders resolver diagnostics;
- if a redaction cannot be applied because the credential set is not yet known at that point in the
  resolver, say so and stop rather than shipping a partially redacted diagnostic.

## Files

[`core/loader.py`, `tests/test_input_ownership.py`, `tests/test_main.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing
it.

## Tests

In `tests/test_input_ownership.py` and `tests/test_main.py`: the finding's reproduction — a credential
value used as a family key, and the same value used as a platform key — raises a refusal whose message
contains neither the secret nor any prefix of it, while still naming the family vocabulary and the
offender; the same two cases through `--check-config` produce the same redacted text; a refusal with no
credential involved is unchanged (assert its full text, so the redaction is not over-broad).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,600 passed / 26 skipped, the count when this gate ran.

## Dependencies

[P22FP22F3] — the previous gate fix lands first; all its dependencies are already merged into `main`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P22F4 — ...`). The scope MUST be `phase5`.
- Keep every phase-0 to phase-4 guarantee in force, and every phase-5 decision: the bridge stays
  optional in every case, its events may replace the basic inputs but never run in parallel with them,
  and an execution the instance cannot confirm is never reported as a success.
- Never weaken, skip, xfail or delete an existing assertion to make the suite pass. If an existing
  test encodes the behaviour this finding calls a defect, replace it and cite the finding id and the
  requirement it restores in the test docstring.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could not do
  because this fix step did not cover it.
