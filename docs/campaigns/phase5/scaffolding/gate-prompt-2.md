# Phase 5 — full-branch closing gate (second pass)

This is the **second pass**: the first pass's report is `docs/campaigns/phase5/full-branch-review.md`
and it refused approval with the four findings quoted below. Four fix steps landed since — `P22F1`
(the ownership guard covers every public publication route), `P22F2` (the installed-writer canary
detects implicit suspension), `P22F3` (a cue is never recorded as the viewer's speech) and `P22F4`
(ownership diagnostics never echo a configured credential). Their specs are in
`docs/campaigns/phase5/scaffolding/steps/P22F1-spec.md` … `P22F4-spec.md`.

**Write your report to `docs/campaigns/phase5/full-branch-review-2.md`.** Writing THAT ONE FILE is
explicitly authorized and required. Create, stage or modify nothing else — no `git add`, no commits, no
other file.

## First job: settle each finding of the first pass

For every finding below, re-run its own reproduction (they are executable and self-contained) against
the current branch and state RESOLVED / PARTIAL / UNRESOLVED with the evidence — the observed output,
not an assertion that it looks fixed. A finding is RESOLVED only if its reproduction now shows the
behaviour the report demanded, and if the fix did not remove the guard rather than close the hole.

### F1 — HIGH: public supervision publication bypasses input ownership

Locations: `core/runtime.py:1128` (ModuleSupervision.emit), `core/runtime.py:1137` (record_and_emit), and `core/runtime.py:1503` (for_module wiring).

Only bus/triggers/chat receive the shared ownership guard. ModuleSupervision delegates both publication methods to Supervision, whose publisher holds the unguarded bus. A module needs no private attribute or forged source to bypass the new rule. Its ordinary public supervision surface accepts channel.chat.message.

Executed reproduction:

~~~python
import asyncio
from test_input_ownership import _guarded_runtime, _intruder, _chat_event

async def main():
    runtime, _, _ = _guarded_runtime()
    context = _intruder(runtime)  # owns chat/y; native owns chat/x
    seen = []
    runtime.bus.subscribe("channel.chat.message", lambda event: seen.append(event))
    event = _chat_event("x")
    await context.bus.publish(event["type"], event["payload"], event["metadata"])
    print(len(seen), context.supervision.snapshot()["input_ownership_rejections"])
    await context.supervision.emit(
        event["type"], event["payload"], metadata=event["metadata"])
    print(len(seen), context.supervision.snapshot()["input_ownership_rejections"])
    await context.supervision.record_and_emit(
        lambda: None, event["type"], event["payload"], metadata=event["metadata"])
    print(len(seen), context.supervision.snapshot()["input_ownership_rejections"])
    assert len(seen) == 2

asyncio.run(main())
~~~

Observed `0 1`, then `1 1`, then `2 1`. The two bypassed publications reach the subscriber, neither increments the rejection counter nor adds its rejection diagnostic. This violates R3's all-subscriber guarantee even though AC13's bus-only example passes. Close every public module publication route over the same guard, or forbid input publication through supervision; cover both methods.

---

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

---

### F3 — MEDIUM: a cue about a viewer is recorded as that viewer's speech

Locations: `modules/brain/__init__.py:3277` (memory eligibility), `modules/brain/__init__.py:3368` (note construction); conflicting guide claim at `docs/automation-bridge.md:258–260` and its translation.

The guide says viewer_memory acts on ordinary messages only. P11's audit explicitly warned that the brain's post-run recording is separate from viewer_memory's bus handler. The current eligibility check excludes reserved colon identities, so anonymous instance cues are safe, but a valid cue with user_id enters memory.record with the operator's cue text as viewer_text. A successful reply is also attached as a confirmed viewer exchange.

Executed through the real bridge normalizer, brain and executor, with a recording memory provider (no persistent files written):

~~~python
import asyncio
from dataclasses import replace
import test_brain as b
from modules.streamerbot import cue_event

async def main():
    factory = b.owned_rules(
        replace(b.MEMORY_RECORD_PROPOSABLE_SPEC, model_proposable=False),
        rules=[b.BRAIN_GRANT, b.grant_to(b.MEMORY_RECORD, b.PRINCIPAL)],
    )
    h = await b.activate_with(
        b.FakeResponse(200, b.completion("Back soon.")), context_factory=factory)
    try:
        p = b.chat_payload("")
        envelope = dict(
            cue_id="cue-memory-1", platform=p["platform"], channel_id=p["channel_id"],
            user_id=p["author"]["id"], user_name="Viewer", text="!brb")
        e = cue_event({
            "event": {"source": "Custom", "type": "Event"},
            "data": {"args": envelope},
        }, {})
        await h.bus.publish(e["type"], e["payload"], e["metadata"])
        (record,) = await h.completed()
        print(e["payload"]["kind"], record.status)
        calls = factory.providers[b.MEMORY_RECORD].calls
        print(calls)
        assert calls == [{
            "viewer_text": "!brb", "delivery": "confirmed", "reply_text": "Back soon.",
        }]
    finally:
        await h.close()

asyncio.run(main())
~~~

Observed `cue success` and exactly the asserted memory-record call. This attributes automation instructions to a viewer and contradicts the operator guide. Resolve the P11 finding by excluding cues from automatic viewer-memory recording, with an end-to-end regression for both user-bearing and instance-author cues. If attribution is deliberately retained, it needs an explicit operator decision and corrected documentation; the current claim cannot stand.

---

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

## Second job: everything the first pass was asked to do

Then repeat the first pass's agenda on the current branch, in full — the requirement map (R1–R10 to
named running tests), the guarantees with an executed counterexample for any claimed failure, the
phases 0–4 regression check, and the hunt for new defects, with particular attention to defects the four
fixes may have introduced (a guard that now refuses legitimate publication, a canary that passes
vacuously, a memory exclusion that also suppressed ordinary recording, a redaction that is partial or
over-broad).

The known-good anchors of the first pass still stand, plus the amendments recorded in
`docs/campaigns/phase5/scaffolding/plan-amendments.md` (D9, D10, D11 and the P12/P14/P15 amendments):
verify their invariants, do not report the changes themselves.

## Deliverable (write to `docs/campaigns/phase5/full-branch-review-2.md`)

- **VERDICT**: `APPROVE` or `REQUEST_CHANGES` (or `REJECT` if unsound)
- **FINDINGS OF THE FIRST PASS**: one line per finding, RESOLVED/PARTIAL/UNRESOLVED, with the observed
  output of its reproduction
- **REQUIREMENT MAP**: R1–R10, each with its named running test and PASS/FAIL/NOT-VERIFIABLE
- **GUARANTEES**: one line per guarantee, with the command or counterexample used
- **NEW FINDINGS**: only with a concrete counterexample or an executed reproduction, each with severity
- **NOT VERIFIED**: stated plainly

This gate CLOSES phase 5. If everything you can check passes, say APPROVE plainly.
