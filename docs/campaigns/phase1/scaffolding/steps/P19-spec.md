# Step P19 — `modules/proxy`: brain-side WebSocket JSON server, remote provider binding, attachments by reference, event adapter (R6; AC32, AC48, AC33-brain, AC35, AC36-brain, AC38, AC41-remote)

Plan step `P19` of the approved Phase 1 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase1/plan.md`
- Approved specification v1.2 (requirement and acceptance-criteria wording): `docs/campaigns/phase1/spec.md`
- Approved v2 design (rationale): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

Manifest v2 (role `input`, `produces: [agent.status]`, `consumes: []`, settings `listen{host, port}`, optional `tls{certfile, keyfile}`, `pairing_token` (credential), `actions[]` allowlist, `max_frame_bytes` (default 1 048 576), `max_attachment_bytes`, `heartbeat_seconds` 15, `heartbeat_timeout_seconds` 10, `late_result_seconds`; validator refuses a non-loopback `listen.host` without `tls`, empty allowlist entries, non-finite bounds; declares no action). `prepare`: for each allowlisted name take the spec from `self._actions.discovered()` (unknown → diagnostic naming `actions[<i>]`, not ready) and `bind` a `_RemoteProvider` over the spec's declared destinations (an overlapping local binding raises the registry's `AmbiguousBindingError` → preparation fails with the ambiguity diagnostic naming the action and both providers, AC41); do not `mark_ready`. `start_inputs`: open the `aiohttp.web` server (TLS context when configured; a `server_factory` seam and a `connection_handler(ws)` entry usable directly with P7's in-memory pair). Session handling per R6: only `hello` before pairing (else `error unknown_session`, `id: null`, connection kept), `v ≠ 1` → `unsupported_protocol_version` + close 4400, missing/empty `hello.id` → `invalid_frame` + 4400, token compared with `hmac.compare_digest` → `auth_failed` + 4401, second agent → `agent_limit` + 4409 while the first stays, text frame over `max_frame_bytes` → `frame_too_large` + 4413, unknown `type` → `unknown_frame` kept; `welcome` echoes the hello `id`, issues `session_id` (rng-injected), lists accepted actions (each declared spec compared for equality with the catalog's — `delivery` included — a mismatch excluded with `error action_mismatch`) and brain limits; then `mark_ready` for the accepted actions only (a per-action readiness: unaccepted actions stay bound but the registry's module readiness is all-or-nothing, so the module tracks accepted names and the provider answers `refused provider_not_ready` for a non-accepted one). `_RemoteProvider.invoke`: assigns `seq` (per session, from 1), sends `call` with the `ActionCall` fields, `deadline_utc`, `remaining_ms` from the call deadline on the brain clock, `max_attachment_bytes`; awaits the `observation` for that `call_id`; `attachment` header + exactly one binary frame → `AttachmentStore.put(run_id, ...)` under the calling run, `attachment_ack accepted` or `attachment_too_large`/`store_full`/`unexpected_binary`; `image_ref` parts must name acknowledged ids (else the observation is `invalid_result` at the executor). Disconnection (close, heartbeat timeout — `ping` every `heartbeat_seconds` on the injected sleeper, no `pong` within `heartbeat_timeout_seconds`): `mark_not_ready`, every in-flight call resolved `external_unknown` (`proxy_disconnected`) for `write` and `error proxy_disconnected` (`retryable: true`) for `read`, `mark_emitted` set for writes after the `call` frame left; nothing retransmitted; an `observation` for a terminal or unknown call increments `late_observations`. `cancel` sent best-effort on run cancellation/deadline. `event` frames: only `agent.status`, payload validated, published with `metadata.source = "proxy"`, `metadata.provider_id = agent_id` overriding whatever the frame carried; `channel.*` and supervision types → `event_not_allowed`. No bus subscription crosses the wire.

## Requirements

Plan mapping: R6
Acceptance criteria owned by this step: AC32, AC48, AC33, AC35, AC36, AC38, AC41
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

[`modules/proxy/__init__.py`, `modules/proxy/module.yaml`, `tests/test_proxy.py`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P2, P3, P7, P18]

All dependencies are already merged into `main` when this step runs.

## Tests

`tests/test_proxy.py` over `MemoryWebSocketPair` driving `connection_handler`: AC32; AC48; the `hello` spec comparison (identical → accepted, a `delivery`-less declaration → `action_mismatch`, others accepted); AC33 brain side (a scripted agent end answering `call` with an attachment header + binary + observation → the executor adopts the `image_ref` from the brain's store after exactly one ack, the observation frame has no path); AC35 (drop with a write / a read in flight; actions absent from `authorized()` until re-pair); AC36 brain side (late and duplicate observations counted); AC38; heartbeat drop on the injected clock; AC41 (both `capture` and `proxy` enabled → ambiguity diagnostic naming `screen.capture` and both providers); `max_frame_bytes` bound on a 1 048 577-byte text frame.

Test command (must pass): `python3 -m pytest tests/ -q -p no:cacheprovider`
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports.

## Risks

Non-trivial (network boundary, concurrency, invariant: at most one paired agent; one observation per call). `aiohttp.web` reads text frames whole — the size bound must be enforced by `WebSocketResponse(max_msg_size=...)` and by an explicit length check for the in-memory path. Binding at `prepare` while marking ready only on pairing means a `screen.capture` call before pairing reads `refused provider_not_ready` (P3), which is the spec's intent.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase1): ...`, `fix(phase1): ...`, `test(phase1): ...`, `refactor(phase1): ...`).
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
