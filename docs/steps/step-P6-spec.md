---
name: "step-audit-module"
author: "adversarial-plan"
status: "draft"
targets:
  - file: modules/audit/module.yaml
    description: "Audit middleware manifest"
  - file: modules/audit/__init__.py
    description: "Audit middleware: non-blocking stdout logging, secret redaction"
  - file: tests/test_audit.py
    description: "Audit module contract tests for R6 (AC17-AC18)"
---

# P6: Audit module — middleware logger

## Problem

Audit middleware logs all events to stdout in structured JSON, non-blocking,
with secret redaction. Order 90 (last in chain), never blocks.

## Requirements

- R1: Subscribe to "**" at order 90. Return event unchanged, never block.
- R2: Write 1 JSON line per event to stdout: event type + payload.
  Async writer task so the handler never blocks.
- R3: Recursively redact fields whose key case-insensitively matches:
  authorization, client_secret, access_token, api_key, credential, credentials.
- R4: Failed/delayed writes must not delay or fail event publication.

## Acceptance criteria

- AC1 (R1): Event reaches audit -> 1 parseable JSON line with type+payload.
- AC2 (R1): Handler returns the exact same event object.
- AC3 (R4): Delayed/failed stdout -> downstream handler still invoked.
- AC4 (R3): All 6 sensitive keys at any nesting depth -> [REDACTED].
  Normal text field retains value.
