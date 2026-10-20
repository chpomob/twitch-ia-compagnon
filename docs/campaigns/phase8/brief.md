# Brief — Phase 8: triggers a module can declare (the kernel stops enumerating them)

Project: `twitch-ia-compagnon` (repo `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`,
HEAD `4936e41`). Phases 0–7 are approved; 4,399 tests green; the phase-7 closing gate approved the tree.

## Problem

The product's September decision says every input declares the trigger types it supports, and that the
trigger decides whether an ingested event becomes a run *before* admission and budgets. The code says
something narrower: `BUILTIN_TRIGGER_TYPES` is a closed set of four evaluators
(`probability`, `audience`, `keyword`, `event_kind`), a module may only pick a subset of them, and the
registry refuses anything else outright —

> `triggers.{input}.types[i].name`: "is not a trigger type this runtime can evaluate (...); supported:
> audience, event_kind, keyword, probability"

So the four things the operator asked for are each a kernel change today, and three of them are not
expressible at all:

- **channel points** — no EventSub subscription, no event kind;
- **the operator's button** — no surface, no event kind (only a Streamer.bot-routed `cue` when SB exists);
- **the streamer's spoken keyword** — `audio_input` is an *action* the brain calls, never an input that
  publishes what it hears;
- **a random trigger from time to time** — expressible only as `watch_tick` + `probability`, which is an
  accident of the tick, not a setting.

The event kind vocabulary is closed and ordered (`message, sub, resub, sub_gift, community_sub_gift,
raid, follow, tip, watch_tick, cue`), and that is the right shape — it names what happened, never which
source produced it. What is wrong is that *the trigger types* are equally closed to the modules while the
product's architecture says they belong to the inputs.

## Decisions already made by the operator (binding; a specification that narrows them is wrong)

- **1 — A module may declare trigger types, and the kernel evaluates them.** A type is declared in the
  module's manifest with its name and a parameter schema, and carries a **declarative match rule** over
  the event payload: a small closed vocabulary of operators (`field`, `equals`, `in`, `gte`/`lte`,
  `matches`, `all`, `any`, `not`). The kernel keeps the four generic types, usable by any input on its
  own events (`probability`, `audience`, `keyword`, `event_kind`), and stops refusing a type it did not
  invent. No code runs in the hot path for a declared type.

- **2 — Where a rule cannot be declared, a bounded hook.** One type in this phase genuinely needs
  computation: a wake word is not a field, it is a detection. A module may therefore supply a `matches`
  callable, called only on events that module itself emitted, under a hard timeout, with its outcome
  recorded like every other trigger decision. A hook that raises, or that exceeds its bound, decides
  **no trigger** — fail-closed, never a silent pass. The hook may not return a run, only a candidacy.

- **3 — A declared type may narrow, never widen.** A type can only restrict what the input already emits;
  it cannot assert a field the input does not produce, and it cannot grant anything. Configuration is
  still validated against both the module's declared schema and the kernel's canonical one, as today.
  Admission, budgets and DEFAULT-DENY are untouched: a trigger decides candidacy, nothing more.

- **4 — Channel points are a first-class trigger.** The Twitch input subscribes to
  `channel.channel_points_custom_reward_redemption.add`, normalizes it to a new event kind `reward`, and
  declares a `reward` type keyed on the reward's identifier with an optional cost range. The vocabulary
  grows by appending, as `cue` did in phase 5.

- **5 — The operator has a button, native and without Streamer.bot.** A new `operator` input publishes a
  `manual` event kind when the operator presses: a local page served by the companion (token-gated, as the
  configuration UI is), a global hotkey, and a button inside an image (an overlay browser source that
  sends the press back). The three surfaces are one event with one button identifier; which surfaces are
  enabled is configuration. Streamer.bot stays optional and its `cue` stays as it is.

- **6 — The streamer's voice is an input, and its keyword is a wake word.** `audio_input` can publish
  what it hears as events carrying the transcribed text and who spoke, so `keyword` works on a spoken
  keyword like it does on chat. The wake word itself is the audio input's own declared type, implemented
  through the hook of decision 2, with the **detector configured, never embedded**: a local command
  (`{kind: command, argv}`) or an HTTP endpoint, exactly as `sources` and `transcription` already work.
  The core gains no dependency. Continuous audio and always-on transcription are **out of scope** and
  documented as a future, deliberately expensive plugin.

- **7 — A timed random trigger is a setting, not a tick.** A `timer` input declares an `interval` type
  (every N minutes, with a probability P and an optional active window), so "say something at random from
  time to time" is configured directly instead of being derived from `watch_tick` plus `probability`.

## What this phase must not do

- Add a runtime dependency to the core (`aiohttp`, `PyYAML` stay the only ones).
- Let a declared type, a hook or a surface bypass admission, budgets, or the authorization of any action.
- Make Streamer.bot required for any of it.
- Turn continuous listening on by default, or record audio that no run asked for.
- Touch the shipped profiles' behaviour for a profile that enables none of this.

## Shape of the work

The kernel change first (the registry, the matcher, the hook, the invariants), then the vocabulary
(`reward`, `manual` appended), then the three inputs that use them (`operator`, `audio_input` as an
input, `timer`), then the Twitch subscription, then the operator guide and a phase summary. Every
trigger family must be demonstrable from a fake source: no test opens a socket, no test needs Twitch or a
microphone, and the real-instance trial stays a documented manual procedure with its own record.
