# Phase 2 — scene-provider integration trial: raw evidence (recorded 2026-09-22)

This is the raw evidence the phase 2 README trial record (step P20) must cite. It was produced BEFORE the
`stream_control` module existed, on purpose: it proves the provider boundary the module has to speak, against
a real streaming application, so the module's contract is not designed against an assumption.

## Environment

- OBS Studio **30.0.2.1-3build1** (Ubuntu 24.04 package, installed by the operator at the arbiter's request)
- **obs-websocket 5.3.4** (built in, RPC version 1), server enabled on `ws://127.0.0.1:4455`, authentication
  required
- Credentials live OUTSIDE the repository: `OBS_WEBSOCKET_URL` and `OBS_WEBSOCKET_PASSWORD` in
  `~/.hermes/.env` (never a repository value, never a shipped default — see AC43)

## Probe

`docs/campaigns/phase2/scaffolding/obs-trial-probe.py`, run with the project virtualenv and the two
environment variables above. It performs the exact request sequence a scene provider needs:
`Hello (op 0)` → `Identify (op 1, obs-websocket v5 auth string)` → `Identified (op 2)` →
`GetSceneList` → `CreateScene` → `SetCurrentProgramScene` → `GetCurrentProgramScene` (read-back).

Its authentication implementation was cross-checked against the reference Python client (`obsws-python`
1.8.0): the two implementations are byte-for-byte equivalent, and both initially failed with close code
**4009 "Authentication failed"** until the operator set a known password through the OBS UI — evidence that
this build of obs-websocket keeps its generated password in memory and does not persist it.

## Observed output (verbatim, 2026-09-22 20:1x)

```
Identified with obs-websocket 5.3.4 | negotiated RPC 1
GetSceneList OK | scenes: ['Scène'] | current: Scène
CreateScene OK | Trial phase2
SetCurrentProgramScene + read-back: Trial phase2
exit=0
```

## What this establishes for phase 2

- the `websocket` scene provider kind can implement `list scenes` / `current scene` / `set scene` against
  obs-websocket protocol version 5 exactly as spec R6 and decision 7 describe;
- **confirmation is a read-back** of the current program scene after the set, not an acknowledgement — the
  probe demonstrates that operation and its observable result;
- the scene name is operator data (the allowlist lives in module configuration), never a code literal;
- the failure mode to expect when the dependency is absent or misconfigured is a **close code** on the socket
  (`4009 Authentication failed` observed), which the module must turn into a value-free not-ready reason when
  `required: false` (AC43), never into a silent success.

## Reproduce

```bash
cd /media/chpo/HDD-papa/twitch-ia-compagnon
set -a && . ~/.hermes/.env && set +a
OBS_TRIAL_SCENE="Trial phase2" .venv/bin/python docs/campaigns/phase2/scaffolding/obs-trial-probe.py
```

Exit codes: `0` provider answered (and, when `OBS_TRIAL_SCENE` is set, the set + read-back matched),
`2` skipped (no credentials — the trial is opt-in), `1` failure with the reason printed.
