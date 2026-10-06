# twitch-ia-compagnon

> **⚠️ Concept / early preview — NOT a proven tool.**
> This project is an architectural exploration, built in the open. It has never driven a real stream,
> its platform adapters have never talked to the real Twitch, Kick or YouTube APIs, and it has no
> production track record. Most of what is asserted below is proven by an automated test suite against
> fakes — which is not the same thing as being proven in the field. Read [What is proven, and what is
> not](#what-is-proven-and-what-is-not) before you invest any time in it.

A **multi-platform AI companion for streamers**: a multi-turn agentic brain whose input and output
**modules are its tools**. A module proposes an action, an executor runs it, and the observation returns
into the loop — the companion can read the chat, remember viewers, speak through your stream audio,
change your scene, clip a moment, or moderate, depending on what you enable and authorize.

Twitch is one channel among several; the core is platform-neutral by construction.

## What is proven, and what is not

**Proven by the test suite** (3,051 passing tests, 18 environment-dependent skips):

- The kernel: bounded admission, explicit terminal outcomes for every action call, a single global
  startup/shutdown deadline, bounded retention, and secret redaction in traces and diagnostics.
- **Default-deny authorization**, reads included: nothing happens without a rule the operator wrote.
- 17 modules with declared manifests and machine-readable settings schemas.
- The configuration UI: a separate process, loopback-bound, token- and CSRF-guarded, generating one page
  per module from its manifest, writing an **overlay** and never the base file.
- The full specification/plan pipeline output, including nine closing-gate passes whose reports are in
  this repository (see [How this was built](#how-this-was-built)).

**Explicitly NOT proven — treat these as unknown, not as working:**

- **No real stream has ever been run with it.** There is no field experience of any kind.
- **The platform adapters have never been exercised against the real platforms.** Twitch, Kick and
  YouTube are covered by tests using fake transports. Their authentication, rate limits, message formats
  and edge cases are unverified against the live services.
- **The configuration UI's browser layer is unverified.** Its HTTP contract and rendering are tested;
  **no real browser session, no JavaScript execution, and no live load test has been performed.**
- **Provider trials are skipped by default.** The audio (TTS/STT), scene-control and platform trials are
  opt-in test nodes that require your own credentials; they are collected and skipped in a normal run.
  One real integration trial was performed during development (an OBS `obs-websocket` v5 scene change),
  and its record is in `docs/campaigns/phase2/scaffolding/obs-trial-record.md`.
- **No production hardening.** No deployment story beyond "run it on your machine", no upgrade path, no
  multi-user operation, no load or soak testing.
- The configuration model is heavy: a ~500-line YAML profile plus up to 25 environment variables. The UI
  is the intended answer to that, and it is new.

If you want something dependable today, this is not it yet. If you find the architecture interesting —
or you want to help make it real — read on.

## Architecture

```
          ┌─────────────── core/ ───────────────┐
 inputs → │  bus · admission · triggers · brain │ → outputs
          │  actions (default-deny) · audit      │
          └──────────────────────────────────────┘
              ↑ manifests                 ↓ observations
     modules/twitch · kick · youtube · chat_context · users · capture · clips ·
     audio_input · audio_output · stream_control · viewer_memory · moderation ·
     watch · brain · proxy · agent_link · audit
```

- **The brain** runs a bounded multi-turn loop: it reasons, proposes a *read* action, receives the
  observation, and ends on an explicit terminal outcome. It never selects the **delivery** — the
  operator configures an ordered list of delivery actions.
- **Every action is authorized explicitly.** There is no implicit allow; a destination without a rule is
  refused, reads included.
- **Every call is bounded**: per-action deadlines, budgets, a bounded queue per session, and stale-work
  dropping that is recorded rather than silent.
- **Two deployment profiles**: everything on the streaming PC, or the brain on a remote host with a
  PC-side agent over a JSON proxy (`docs/proxy-protocol.md`).

## Quick start

Requires Python 3.10+.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"

# 1. Look at a shipped profile before writing your own.
ls *.example          # config.yaml.example (PC profile, chat + audio + scenes)
                      # presence.yaml.example (the full phase-3 profile: persona, routes, memory…)
                      # config.server.yaml.example + agent.yaml.example (the remote profile)

# 2. Validate a profile without opening a single socket (exit 0 = accepted, 2 = refused).
python -m core.main --config presence.yaml.example --check-config

# 3. Run the companion.
python -m core.main --config my-config.yaml

# 4. Serve the local configuration UI (loopback only, prints a one-time access URL).
python -m twitch-ia-compagnon-config-ui --config my-config.yaml

# 5. The test suite.
python -m pytest tests/ -q
```

### Configuration in two minutes

- **Secrets are never literals.** A profile refers to `${NAME}` environment references; the runtime
  resolves them and redacts them everywhere. The UI shows whether a reference is set — never its value.
- **The UI writes to an overlay** (`config.local.yaml` next to the base file), merged over the base, so
  the commented example stays intact. Removing an override restores the base value.
- **Applying a change is a supervised restart** — there is no hot reload in this version.
- `secrets` and the authorization rules (`actions`) are **read-only in the UI**: a single mis-click must
  not be able to widen what the companion is allowed to do.

## Project layout

| Path | What it is |
|---|---|
| `core/` | The kernel: bus, admission, triggers, brain loop, actions, loader, runtime, audit |
| `core/config_ui/` | The local web configuration UI (separate process) |
| `modules/` | 17 modules, each with a `module.yaml` manifest declaring its actions, schema and capabilities |
| `tests/` | 3,051 tests, plus opt-in real-integration trials |
| `docs/design-v2.md` | The design blueprint the phases implement |
| `docs/config-ui.md` | Operator documentation for the configuration UI |
| `docs/proxy-protocol.md` | The remote-deployment proxy protocol |
| `docs/campaigns/phase{1,2,3,4}/` | The specifications, plans, per-step specs, gate prompts and **gate reports** of each delivery phase |

## How this was built

This project is developed through an **adversarial pipeline**, and its records are part of the
repository:

1. A **specification** is written from a brief, then challenged by a different model until it holds.
2. A **plan** breaks it into ordered, independently verifiable steps.
3. Each step runs as its own loop: one model implements, a **different** model reviews, and the step
   lands as a single atomic commit with a green suite.
4. A **delivery phase ends with a full-branch gate** that reviews the whole cumulative diff, replays its
   own counterexamples and postulates new defects.

That last part is where this repository is most honest about itself. Phase 4's gate took **nine passes**;
each pass re-ran the previous counterexamples and reported what it could still break. It found 13 real
defects — including a configuration path that could overwrite the base configuration file, a secret
disclosure for short secret values, and three defects **introduced by earlier fixes** — every one
reproduced. `docs/campaigns/phase4/scaffolding/gate-report-1..9.md` are those reports, unedited.

## License

**WTFPL v2** — *Do What The Fuck You Want To Public License* (see [`LICENSE`](LICENSE)). In practice: use
it, fork it, ship it, commercially or not, with no conditions. It comes with **no warranty of any kind**
and **no support** — which is the honest position for a preview that has never run a real stream.

## What would make this real

- Running it against live Twitch, Kick and YouTube channels, and recording what actually breaks.
- Exercising the configuration UI in a real browser, with a real editing session.
- A deployment story (packaging, upgrades, supervision) and load testing.
- Closing the loops the trials open: the opt-in trial nodes are the checklist of what remains unverified.
