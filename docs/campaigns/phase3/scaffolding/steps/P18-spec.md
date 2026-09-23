# Step P18 — `modules/kick`

Plan step `P18` of the approved Phase 3 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase3/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase3/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

- **Manifest v2.** Role `input`.
    - Trigger types `probability`, `audience`, `keyword` and `event_kind`,
      with default policy "mentions `companion_name`".
    - `chat.write` for `kick/*/chat`, declared in P19.
    - Credentials `client_secret` and `access_token`.
    - Settings: `listener.host`/`listener.port`, `public_key` (PEM, optional;
      otherwise fetched once at prepare through the injected transport),
      `channels`, `companion_name`, `notices.kinds`, dedup bounds. Plus the
      validator.
  - **Listener.** An `aiohttp.web` listener on the configured host and port.
  - **Accepting a delivery.** A delivery is accepted only if all of these
    hold, checked in this order:
    1. body ≤ 65536 bytes (read with a bounded reader);
    2. `Kick-Event-Signature` verifies (decision 9) over `message id + "." +
       timestamp + "." + raw body`;
    3. the timestamp is within 300 s of the injected clock;
    4. the message id is not in the bounded dedup window.
  - **Refusals** answer 4xx and count a per-reason counter, never
    publishing.
  - **Mapping.** `chat.message.sent` → `message`, `channel.followed` →
    `follow`, `channel.subscription.new` → `sub`,
    `channel.subscription.renewal` → `resub`, `channel.subscription.gifts` →
    `sub_gift`.
  - **Roles.** Badges `broadcaster`, `moderator`, `vip` and `subscriber`
    become trusted roles.
  - **Pipeline.** Anti-echo on the companion identity; the `:` refusal; the
    chat-context feed; trigger evaluation; admission, as twitch does.
  - **`conftest.py`** gains `KICK_TEST_KEY` (a fixed RSA test key pair: PEM
    public key plus integer private exponent, labelled test-only) and
    `SignedWebhookSender(key, clock)`, which signs with `pow(m, d, n)` and
    posts through an in-process `aiohttp` test client.
  - **Packaging.** Add the pyproject line and the running catalog counts.

## Requirements

Plan mapping: R7, R1, R6
Acceptance criteria owned by this step: AC34, AC35, AC32
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.
Phase-1 product decision that overrides any contrary reading: delivery is a **configured, pluggable
terminal step** — a configured ordered list of delivery actions (mode `fixed`, or mode `modules`
derived from the enabled modules that declare a delivery capability, with a configurable preference
order), each entry declaring how the answer text maps into its arguments (a named argument, or none
for an effect-only delivery such as a stream-scene change); every entry runs at the terminal step
only, each receiving the text only if declared; the model never selects the delivery and never
performs an intermediate effect; adding a delivery module must require no change to the agentic loop.

## Files

[modules/kick/__init__.py, modules/kick/module.yaml, pyproject.toml, tests/test_kick.py, tests/conftest.py, tests/test_examples.py, tests/test_profiles.py]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P4, P7]

All dependencies are already merged into `main` when this step runs.

## Tests

- AC34: a valid delivery is published once as a `kick` event and
    admitted per policy. A bad signature, a 301 s-old timestamp, a
    65537-byte body and a duplicate id are each refused, counted and yield 0
    events. The listener binds the configured host/port (loopback, port 0
    resolved).
  - AC35 partial: the badge → audience checks and the four event-kind
    mappings.
  - AC32 kick half: an author with `:` gives 0 admissions and invalid + 1.
  - The verifier rejects a signature ≥ n and a wrong DigestInfo.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

- Kick's exact signature input and header names must match the public
    webhook documentation. The tests pin the documented shape, and the P24
    trial validates it against the real platform.
  - A committed RSA private key could trip a secret scan. Mitigation: name
    it test-only and keep it inside `tests/`. The hygiene secret check
    targets settings shapes in the README, not tests.
  - Webhook reachability (public HTTPS) is a deployment prerequisite,
    documented in P25 and not provided.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase3): ...`, `fix(phase3): ...`, `test(phase3): ...`, `refactor(phase3): ...`).
- Keep the phase-0 guarantees in force: bounded admission, phase lifecycle, explicit terminal action
  outcomes, default-deny authorization (reads included), bounded retention, a single global
  startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss diagnostics.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- No new runtime dependency beyond the existing ones unless this step is the one that adds it; no
  model, provider or vendor name in code, config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
