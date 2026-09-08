---
name: "step-config-example"
author: "adversarial-plan"
status: "draft"
targets:
  - file: config.yaml.example
    description: "Example configuration file with env var references for secrets"
  - file: tests/test_examples.py
    description: "Cross-manifest coherence and config sanity checks"
---

# P7: Example configuration + cross-manifest checks

## Requirements

- R1: config.yaml.example has non-empty modules_directory, enables exactly
  twitch, brain, audit. Each module has non-empty settings with secret values
  as ${NAME} env refs and non-secret values as sample values or env refs.
- R2: All 3 manifests (twitch, brain, audit) have unique names, correct
  produces/consumes directions, Boolean middleware, integer order for audit.
- R3: The example config is parseable YAML with no usable credential literals.

## Acceptance criteria

- AC1 (R1,R2): Manifests parse, have correct directions, audit has
  middleware true + order 90.
- AC2 (R1): Config parses, has modules_directory, enables all 3, each
  setting present.
- AC3 (R3): No literal credential values (no usable api keys or tokens).
