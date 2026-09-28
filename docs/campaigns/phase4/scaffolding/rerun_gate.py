#!/usr/bin/env python3
"""Re-run the phase-4 closing gate exactly as the campaign's runner does.

Why this exists: the campaign's own gate run (2026-09-28 22:26) was cut off because Codex hit its
usage limit IN THE MIDDLE of the review, so no report was written and the runner recorded
"unparsed". The gate is not done — it must be re-run on a fresh reviewer window.

This launcher imports the campaign runner and calls its own `run_gate`, so the wait-for-reset loop,
the command, the captured log and the verdict parsing are identical to a campaign gate. It waits
until Codex has a window (its 5h reset), then reviews the whole branch.
"""
import importlib.util
import pathlib
import sys
import time

RUNNER = pathlib.Path('/media/chpo/HDD-papa/twitch-ia-compagnon/docs/campaigns/phase4/scaffolding/run_campaign_phase4.py')

spec = importlib.util.spec_from_file_location('phase4_runner', RUNNER)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

verdict, report = runner.run_gate(time.time() + 6 * 3600)
print(f'GATE_VERDICT={verdict}')
print(f'GATE_REPORT={report}')
sys.exit(0 if verdict == 'APPROVE' else 1)
