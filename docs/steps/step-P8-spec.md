---
name: "step-integration"
author: "adversarial-plan"
status: "draft"
targets:
  - file: tests/test_integration.py
    description: "End-to-end integration test: Twitch -> Brain -> Audit -> Helix"
---

# P8: Integration test

## Requirements

- R1: Load the example config, activate all 3 real modules with injectable
  HTTP/WebSocket/output boundaries, drive one EventSub notification through
  the full pipeline, assert one Helix request and two audit records.
- R2: Also prove isolation: after a failed publication, a later independent
  publication still succeeds.
- R3: Config-driven values (endpoint, model, credentials) are proven to come
  from config/env, not from Python source code.

## Acceptance criteria

- AC1: 1 EventSub notification -> 1 Brain LLM call -> 1 channel.chat.send
  -> 1 Helix POST -> 2 audit records (input + output). Clean shutdown exit 0.
- AC2: Failed publication -> sanitized diagnostic -> later publication succeeds.
- AC3: Grep of Python sources finds 0 literal occurrences of endpoint, model,
  broadcaster_id, bot_user_id, credentials.
