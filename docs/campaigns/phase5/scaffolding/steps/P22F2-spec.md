# Step P22F2 — the installed-writer canary detects implicit suspension before the write

Gate fix step of Phase 5. It is **not** one of the plan's numbered steps: it answers finding **F2**
of the phase's closing review (`docs/campaigns/phase5/full-branch-review.md`), which refused approval of
the assembled branch.

### F2 — MEDIUM: the installed-writer canary ignores implicit suspension

Location: `tests/test_automation_bridge.py:2694`.

The canary searches for ast.Await and pre-write raises. An async-with acquisition suspends without an ast.Await node. The following in-memory change to the installed send_frame source passes the existing canary but blocks before _write_websocket_frame. This is precisely a writer change D8 requires to fail loudly. The actual installed writer is currently sound; the defect is its regression protection.

Executed reproduction:

~~~python
import asyncio, inspect, textwrap
from types import SimpleNamespace
from unittest.mock import patch
from aiohttp._websocket.writer import WebSocketWriter
from test_automation_bridge import (
    test_d8_canary_the_installed_client_writes_before_its_first_suspension as canary,
)

canary()  # current installed writer passes
original = inspect.getsource
source = textwrap.dedent(original(WebSocketWriter.send_frame))
needle = "        self._write_websocket_frame(message, opcode, 0)"
assert source.count(needle) == 1
changed = source.replace(needle, "        async with self._send_lock:\n    " + needle)
with patch.object(inspect, "getsource", side_effect=lambda fn:
        changed if fn is WebSocketWriter.send_frame else original(fn)):
    canary()  # unexpectedly passes
namespace = dict(WebSocketWriter.send_frame.__globals__)
exec(compile(changed, "<in-memory-canary-counterexample>", "exec"), namespace)

async def main():
    writes = []
    lock = asyncio.Lock()
    await lock.acquire()
    writer = SimpleNamespace(
        _closing=False, compress=0, _send_lock=lock,
        _write_websocket_frame=lambda *args: writes.append(args),
        _output_size=0, _limit=1000,
    )
    task = asyncio.create_task(namespace["send_frame"](writer, b"frame", 1))
    await asyncio.sleep(0)
    print("writes:", len(writes), "pending:", not task.done())
    assert not writes and not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

asyncio.run(main())
~~~

Observed: original canary PASS; changed canary PASS; `writes: 0 pending: True`. With this library behaviour the provider would already have marked emission while the frame had not left. Add a behavioural check of the actual uncompressed client/writer path and negative controls for pre-write suspension, including implicit awaits; keep the loud fallback diagnostic.

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


The canary must fail loudly when the installed writer could suspend **before** the frame is handed to the
transport, whatever the syntactic form of that suspension. Reading the AST for an explicit `await` is
not enough: an `async with` (a lock acquisition, for instance) suspends without an `Await` node, which is
precisely the library change deviation D8 must catch.

Requirements:
- add a **behavioural** check of the real uncompressed client/writer path used by the bridge
  (`compress=0`): the frame reaches the transport in the same synchronous segment as the emission mark;
- add **negative controls** that must fail with the loud diagnostic if a suspension appears before the
  write — the finding's mutated source, which suspends via `async with`, is one; make the control run
  against the mutated source rather than asserting a property of the current one;
- keep the documented fallback route (a module-local writer) named by the failure message;
- the check must be honest about its own limits: if it cannot be made behavioural without a socket, say
  what it does instead of pretending.

## Files

[`tests/test_automation_bridge.py`]

Touch ONLY these files. If you believe another file must change, stop and report it instead of editing
it.

## Tests

In `tests/test_automation_bridge.py`: the existing canary keeps passing on the installed aiohttp; the
negative control built from the finding's mutation (an `async with` inserted before the write) makes the
canary fail with the loud message; the behavioural check observes the frame arriving at the transport
before any suspension point of the bridge's own call.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
The suite must not drop below 3,600 passed / 26 skipped, the count when this gate ran.

## Dependencies

[P22FP22F1] — the previous gate fix lands first; all its dependencies are already merged into `main`.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`fix(phase5): P22F2 — ...`). The scope MUST be `phase5`.
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
