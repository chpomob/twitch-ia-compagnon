# Brief — Phase 9: the first fifteen minutes (wizard, console, ready-made profiles)

Project: `twitch-ia-compagnon` (repo `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`,
HEAD `cb0f5b1`). Phases 0–8 are approved; 4,821 tests green; CI green on Python 3.10–3.13.

## Problem

Everything the product can do is reachable only by someone who already knows how it is built. The
configuration UI is a precise form editor over the YAML — field by field, JSON textareas for lists — with
CSRF, secret redaction and a *Check* button. That is rigour, not accessibility: it assumes the reader
knows the schema, the module names and what a trigger policy is. Before it, the installation itself needs
a terminal, a virtualenv and a `pip install`.

A streamer who is not technical stops at the first of these walls, and the product's own documentation is
written for an operator, not a newcomer. Nothing in the current surface answers the only question that
matters in the first fifteen minutes: *« est-ce qu'il m'a compris, et est-ce que ça marche ? »*

## Binding decisions (operator, 2026-10-22)

- **1 — The audience and the criterion.** The target is the streamer on his own PC, non-technical. The
  phase is done when such a person installs and gets a first run in **under fifteen minutes, without
  opening a terminal and without reading or writing YAML**. The automated part proves each step's
  validation and the generated profile's cleanliness; the fifteen-minute claim itself is an **operator
  trial with a written record**, like the real-instance trials — not a test that pretends to measure
  human speed.

  **Amendment A1 (2026-10-22).** Installing the product without a terminal *is* packaging, and packaging is
  explicitly another phase. The criterion therefore starts **once the package is installed**: from a
  working install, to a first run, under fifteen minutes, without a terminal and without YAML. The
  operator trial must **record how the install itself was done** and how long it took, so the remaining
  gap is visible in the record rather than hidden inside a redefined promise. Phase 10 owns the
  terminal-free installation.

- **2 — One surface, not a new one.** The wizard and the console are part of the existing local web
  application (`core/config_ui`): same binding rules (loopback, token, CSRF, origin checks), same
  redaction rules, no second server and no second token. The UI never renders a secret value, today or
  after this phase.

- **3 — Every wizard step is validated by an observed success.** A step's *next* is only available once
  the product has really seen the thing work: the Twitch credential authenticates and names the channel;
  the model endpoint answers an actual completion; the generated profile passes `--check-config`; the dry
  run produces one recorded run from a fake event. A failure shows a **plain-language cause and the exact
  next action**, never a stack trace and never a value.

- **4 — Four ready-made profiles, and no more.** The wizard's choice screen offers exactly four, each a
  complete valid profile with a `--check-config` pass and a dry run: **chat Q&A** (answers when mentioned),
  **channel points** (reacts to a named reward), **spoken keyword** (the wake word on the audio input), and
  **silent** (everything off, the watchdog profile). More than four is documentation in disguise.

- **5 — A console that answers « est-ce que ça marche ? ».** The same page shows: events arriving, runs
  started and ended, **refusals with their reason in plain language**, modules in `degraded` with the
  cause, start and stop, and revoking a granted action. It drives the same supervisor the command line
  drives — it is not a second control plane, and it cannot do anything the runtime's rules do not already
  allow.

- **6 — The YAML is hidden, then readable, never edited here.** The wizard never asks for YAML; the
  generated profile can be *viewed* read-only on request. Editing stays in the existing editor, unchanged,
  for operators who want it.

- **7 — Bilingual, as the documentation already is.** The new surfaces (wizard, console, the four
  profiles' descriptions) exist in English and French from one string table, chosen by a setting; the
  existing editor stays as it is. A third language must be a data change, not a code change.

- **8 — No new dependency and no new socket in tests.** The Twitch and model validators are exercised
  against fakes; the live feed is bounded and redacted; nothing in the suite opens a network socket or
  starts a process outside the existing, named exemptions.

## What this phase must not do

- Add a capability, a module or a trigger: it makes the existing product reachable, it does not extend it.
- Ship Docker, a Windows installer or any packaging: that is a separate phase, for a different audience.
- Weaken the existing security surface: a token-gated page stays token-gated, and a redacted value stays
  redacted wherever it travels.
- Start anything implicitly: the wizard prepares a profile; only the operator starts the runtime.
- Promise a first run it has not produced: each profile's dry run is recorded, and the trial record says
  what was actually done.

## Shape of the work

First the state and the strings (first-run detection, the string table, the four profiles as data with
their tests), then the wizard's five screens with their validators, then the console's live feed and its
plain-language reasons, then the start/stop and revocation controls, then the guide and the trial record,
then the gate. The four profiles come before the wizard, because a wizard with nothing to offer is a form.
