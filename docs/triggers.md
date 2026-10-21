# Triggers — operator guide

**English** · [Français](triggers.fr.md)

**Status: normative.** This guide is the reference for the trigger types an input declares and for the
four trigger families phase 8 adds: channel points, the operator's button, the streamer's voice and a
timed random trigger. Where its French translation differs, this English text wins.

A trigger decides one thing: whether an event an input ingested becomes a *candidate* for a run. It
grants nothing. An accepted candidate still goes through admission, budgets and default-deny action
authorization exactly like a chat message, and any of them can still refuse it. Everything below is
proven by the test suite against fakes only — a fake platform, fake surfaces, a fake detector, a fake
recorder and an injected clock. No test contacts Twitch, opens a socket or reads a microphone. A real
instance is exercised only by the manual trial at the end of this guide.

## Generic and declared types

Every input declares, in the `triggers` block of its `module.yaml`, the trigger types its events can
be selected by. There are two sorts.

- **Generic types.** Four types any input may declare, each narrowing the kernel's canonical schema:
  `probability` (one draw below a configured probability), `audience` (who wrote the message),
  `keyword` (the text contains one of the configured keywords) and `event_kind` (the event's kind is
  listed). An input may narrow a generic type — `operator` restricts `event_kind` to `manual` — but
  never widen it.
- **Declared types.** A type an input defines for its own events: a `name` (`[a-z][a-z0-9_]*`, at most
  32 characters, none of the four generic names), a `parameter_schema`, the non-empty list of `kinds`
  it applies to, and exactly one of a declarative `match` rule or `hook: true`. The kernel evaluates a
  `match` rule itself: no module code runs in the hot path for it.

A declared type narrows and never widens. Its rule only runs on an event the declaring input
published; a policy that names it for another input is refused at load. An event of a kind outside
the type's `kinds` does not match (`<type>_kind_mismatch`). Every invariant that runs before triggers
today — internal-trace exclusion, normalization, authenticity, anti-echo, the dedup window — still runs
first, unchanged.

A configuration mistake in a declared type or in a policy stops startup before any transport opens,
with a diagnostic naming the input and the offending field path (for example
`triggers.timer.policy.rules[0].parameters.window`) and echoing no configured value.

## The match vocabulary

A `match` rule is a tree of a closed set of nodes: `all`, `any`, `not`, `equals`, `in`, `gte`, `lte`
and `matches`. It is at most 8 levels deep and holds at most 64 nodes. Its leaves are operands:

- `{field: <path>}` — a value of the event payload. The path must be a canonical normalized field
  (`kind`, `text`, `platform`, `channel_id`, `message_id`, `author.id`, `author.display_name`) or a
  field the input declares in `triggers.fields` with its scalar type. A type cannot assert a field its
  input does not emit: a rule that names an undeclared field is refused at load (**A4**, the field
  catalog).
- `{param: <name>}` — a configured parameter of the type, or a dot-separated path into an object
  parameter (`window.start_minute`). A comparison whose parameter is absent from the configuration is
  true, so an optional bound left out does not restrict anything. A parameter that is not required
  may not be used under `not`.
- A literal: a string, a number, a boolean, or a list of at most 64 such scalars.

Comparisons follow the JSON types. `equals`, `gte`, `lte` and `matches` are false when the field is
absent or of another type than the other operand. `in` is true exactly when the list holds an element
of the same type equal to the value. Strings compare case-sensitively, except under `matches`.

**`matches` is a wildcard, not a regular expression (A2).** The pattern is case-insensitive, `*`
stands for any run of characters and `?` for one character, and a pattern is at most 128 characters
long. A regular expression from configuration evaluated on viewer text is a denial-of-service surface;
the wildcard matcher instead takes at most (len(text) + 1) × (len(pattern) + 1) steps, one step being
one comparison of a pattern position with a text position. The engine counts them, and its counts
expose the total as `wildcard_steps`.

**One probability parameter (A3).** The vocabulary has no random operator. A declared type may name one
of its own parameters as its `probability_parameter`: a required number within [0, 1]. Each evaluation
of the rule then draws exactly one value from the engine's injected random source — whether or not the
tree matched — and the rule matches only when the tree matched and the draw is below the configured
probability (otherwise `<type>_probability_miss`). Each such draw is recorded in the decision's
`draws`: the rule's index, the parameter's name, its value, the draw and its ordinal, so a recorded
run can be replayed.

The reasons a declared rule produces are `<type>_match`, `<type>_no_match`, `<type>_kind_mismatch`
and `<type>_probability_miss`, prefixed `accepted:` or `rejected:`.

## Hooked types

Some decisions cannot be written as a match rule. A declared type with `hook: true` delegates its
match to a function its own module supplies at activation; no other module can supply it. The hook is
bounded and fail-closed:

- It is a coroutine function, awaited by the engine's asynchronous entry point and raced against the
  engine's clock for the type's `hook_timeout_seconds`, which lies in (0, 10]. The synchronous entry
  point never calls a hook: it decides `<type>_hook_unavailable` instead, because a hook is only
  reachable where it can be bounded. A hook that blocks the event loop *synchronously* inside its
  coroutine cannot be pre-empted in-process, so a hook must be cooperative; the shipped `wake_word`
  hook is pure and constant-time.
- It receives a read-only view of the event and of the rule's parameters, and it is called at most once
  per evaluation — never for an event an invariant rejected, recalled from the dedup window, of a kind
  outside the type's `kinds`, or published by another input.
- `True` matches (`<type>_match`) and `False` does not (`<type>_no_match`). Anything else is **no
  trigger**: another return value or an exception (`<type>_hook_error`), no answer within the bound
  (`<type>_hook_timeout`), no hook supplied (`<type>_hook_unavailable`).
- A failed hook is a hard veto. After every rule of the policy is evaluated, and outside the policy's
  combination, a failed hook outcome rejects the whole decision with the first failing rule's reason —
  whatever the combination (`all_of`, `any_of`, `none_of`) and whatever else matched. No combination,
  polarity or default can turn a failed hook into an acceptance.

Every hook outcome is recorded like any other decision: counted, traceable and retained in the dedup
window. A hook cannot create a run or change admission, budgets or authorization, and its failure never
stops the ingestion of later events.

## Configuring a policy

A profile selects a policy per input and per channel in its top-level `triggers` block.
A configured policy replaces the default entirely.
Rules are never merged with the default they replace; a channel with no entry of its own keeps the
default the input's manifest declares. A policy has a `combination`
(one the input supports) and a list of `rules`, each naming one type the input declares and the
`parameters` its schema accepts.

Each example below is a fragment: the `modules` part goes under your profile's `modules`, next to the
other settings of that module, and the module goes into `enabled_modules`; the `triggers` part goes
into your profile's `triggers` block. Each example is loaded by the test suite against the shipped
manifests, so it stays valid as the manifests evolve. The `${...}` references are environment
variables, as everywhere in a profile.

## Channel points: `reward`

When `notices.kinds` lists `reward`, the `twitch` input adds the channel-points redemption subscription
(which needs the redemption-read scope for the broadcaster). Each redemption becomes an event of kind
`reward`: its `text` is the viewer's input when they typed one and the reward's title otherwise, and
`payload.reward` carries `{id, title, cost}`. A refused subscription degrades `reward` only; chat
continues. The `twitch` default policy is unchanged: it is the `keyword` rule over the companion's
name, and `keyword` reads the event's `text` whatever its kind. So once `reward` is listed, a
redemption whose viewer input — or, without one, whose reward title — contains the companion's name
triggers under the default, whatever the reward and its cost. Any other redemption triggers nothing
until you configure the `reward` type.

The `reward` type takes `reward_ids` (required: the reward identifiers that may trigger), and the
optional `min_cost` and `max_cost` (integers ≥ 0, both bounds inclusive). A configured policy replaces
the chat default for that channel: to keep answering mentions there as well, add the `keyword` rule
with `any_of`, as below.

```yaml
modules:
  twitch:
    notices:
      kinds: [reward]

triggers:
  twitch:
    channels:
      ${TWITCH_BROADCASTER_ID}:
        combination: any_of
        rules:
          - type: reward
            parameters:
              reward_ids: ["ask-the-companion"]
              min_cost: 100
              max_cost: 5000
          - type: keyword
            parameters:
              keywords: ["Companion"]
```

The `keyword` rule here, too, reads every event's `text`, redemptions included: a redemption of a
reward not listed in `reward_ids`, or outside the cost bounds, still triggers when its input or title
contains "Companion". The `reward` bounds limit what the `reward` rule accepts, not the policy.

Fulfilling or refunding a redemption is out of scope: the companion never changes its status.

## The operator's button: `button`

The `operator` input publishes one event of kind `manual` per accepted press of a configured button.
The event's `text` is the button's `prompt_text`, its author is `operator:<id>` with the button's
label, and `payload.button` carries the button's `id` and the `surface` it was pressed from. Its run
answers on the configured `destination`. Streamer.bot is neither required nor touched.

There are three surfaces, each disabled until enabled in `surfaces`, and at least one must be:

- **`page`** — a local page listing the buttons, at `http://<host>:<port>/?token=<token>`.
- **`overlay`** — a page for a browser source, at `http://<host>:<port>/overlay?token=<token>`. It shows
  the optional `overlay.image` with each button that has a `rect` placed over it, in percent of the
  image. Each click is sent back to the companion.
- **`hotkey`** — the shipped press command, `python -m modules.operator press <button>`.

The listener binds `127.0.0.1` by default; another address needs `allow_non_loopback`, and a wildcard
address is refused. Every request carries the `token` (at least 32 characters, an environment
reference), is compared in constant time and is refused without it; the token never appears in an
event, a trace, a diagnostic or an access log. A press of an unknown button, from a disabled surface,
or within the button's `cooldown_seconds` (default 2) of its last accepted press is refused and
counted. The listener exists only between `start_inputs` and `stop_inputs`.

The default policy accepts every press (`event_kind` with `kinds: [manual]`). The `button` type
restricts a channel to the listed `button_ids`:

```yaml
modules:
  operator:
    buttons:
      - id: hype
        label: Hype
        prompt_text: "Cheer the chat on for the last play."
        cooldown_seconds: 30
        rect: {x: 80, y: 5, w: 15, h: 10}
      - id: recap
        label: Recap
        prompt_text: "Give the chat a short recap of the stream so far."
    destination:
      platform: twitch
      channel_id: ${TWITCH_BROADCASTER_ID}
    surfaces:
      page: true
      hotkey: true
      overlay: true
    listen:
      host: 127.0.0.1
      port: 8765
    token: ${OPERATOR_TOKEN}

triggers:
  operator:
    channels:
      ${TWITCH_BROADCASTER_ID}:
        combination: all_of
        rules:
          - type: button
            parameters:
              button_ids: ["hype", "recap"]
```

**Binding a global hotkey.** The companion binds no key: a global hotkey needs operating-system APIs
the core does not take on. The press command is what you bind, with the tool your system offers for
that — a desktop environment's custom shortcuts, a hotkey daemon, a macro pad's software, or a stream
deck action that launches a program. Bind the key to the command line below, run from the
repository's virtual environment with the environment variable the profile's `token` names set:

```bash
python -m modules.operator press hype --profile /path/to/my-config.yaml
```

It reads the profile (default `config.yaml`), takes the operator's `listen` address and resolves only
the token's `${NAME}` reference, sends one press with surface `hotkey`, and prints no token. It exits
`0` when the input accepted the press, `2` when the input refused it (unknown button, cooldown, token,
surface disabled), `3` when the input could not be reached or did not answer within 5 seconds, and `64`
on a usage or profile error.

## The streamer's voice: `keyword` and `wake_word`

`audio_input` becomes an input when `listen.enabled` is `true` (default `false`: with listening off, no
detector or source ever starts and the module behaves as before). Listening needs a configured wake-word
detector, `wake_word.detector`; none is embedded and none is assumed:

- `{kind: command, argv}` — a program started with no audio from the companion. It waits on its own
  microphone and exits `0` with the matched phrase (at most 64 characters) on its standard output when
  it detects, or `1` when it returns without detecting.
- `{kind: http, endpoint, api_key}` — one request per invocation, carrying no audio. It answers
  `{"detected": true, "phrase": "..."}` or `{"detected": false}`. `api_key` is optional.

Each iteration runs in one order. (1) The detector reports a verdict: detected, and the phrase. (2) The
module submits a *candidate* event of kind `speech` to the trigger engine: its `text` is the
detector's phrase (never a transcription) and `payload.wake_word` is `{detected: true, phrase}`. The
candidate is never published. (3) Only if the policy accepts it, the module acquires one segment from
the `command` source `listen.source` names, of at most `listen.max_seconds` and starting at most
`listen.pre_roll_seconds` before the verdict, and transcribes it once through the existing
`transcription` settings. (4) The run receives a `speech` event whose `text` is that transcription,
admitted under the candidate's decision. A declined candidate acquires and transcribes nothing.

Both trigger types therefore work **over the detector's phrase**, not over a transcription. The hooked
`wake_word` type (no parameters, bound 2 seconds) is the default policy: it matches when the detector
reported a detection. The generic `keyword` type matches when the phrase contains one of the configured
keywords, so it selects *which* wake phrases start a run. In the example, a run starts only when the
detector reported a detection and its phrase contains "hey companion":

```yaml
modules:
  audio_input:
    listen:
      enabled: true
      source: mic
      pre_roll_seconds: 2
      max_seconds: 10
      speaker: streamer
      destination:
        platform: twitch
        channel_id: ${TWITCH_BROADCASTER_ID}
    wake_word:
      detector:
        kind: command
        argv: ["${WAKE_WORD_DETECTOR}"]

triggers:
  audio_input:
    channels:
      ${TWITCH_BROADCASTER_ID}:
        combination: all_of
        rules:
          - type: wake_word
            parameters: {}
          - type: keyword
            parameters:
              keywords: ["hey companion"]
```

**The listen source is not a capture recorder.** `listen.source` names a `command` source under
`audio_input.sources`, but listening runs it differently from `audio.capture`: nothing stops it at a
duration, so it must end the segment itself. It is started with `AUDIO_INPUT_PRE_ROLL_SECONDS`
(= `listen.pre_roll_seconds`) and `AUDIO_INPUT_MAX_SECONDS` (= `listen.max_seconds`) in its
environment, and it must write one 16 kHz mono 16-bit WAV to its standard output that starts at most
that pre-roll before the verdict and lasts at most that maximum, with a header whose sizes state the
data actually written (not a streaming placeholder), then close its output and exit `0` on its own.
Any other outcome — another exit status, a header that does not state the data, a longer segment, more
than `max_bytes` — is a source failure, never trimmed. The `mic` source of the shipped profiles is a capture
recorder that streams until it is stopped: the example above names `mic`, but with that recorder
unchanged every accepted wake word ends as a source failure. Point `mic` (or the source
`listen.source` names) at a program that honours both variables — typically a script reading your
microphone's ring buffer — before enabling listening.

A detector failure — a program that cannot start, exits other than `0` or `1`, or reports a phrase
over 64 characters; an endpoint unreachable or answering another shape — never reads as "no wake word".
It reports `audio_input` degraded once (`wake_word_detector_failed`), and recovery is reported once
with `module.ready`. The detector restarts 1 second after a declined candidate or a published `speech`
event, and `retry_seconds × 2^(n−1)` seconds (capped at 300) after the n-th unproductive iteration in a
row — no detection, a detector failure, a source failure, a failed or empty transcription — so a dead
detector never spins. The segment's audio never reaches the bus, the audit stream or the attachment
store.

## A timed random trigger: `interval`

The `timer` input publishes one event of kind `timer` per configured channel every `every_minutes`
minutes, on the companion's clock, counted from `start_inputs`. Its `text` is the channel's
`prompt_text`. The declared `interval` type decides each firing: `every_minutes` (required, 1–1440),
`probability` (required, 0–1, its probability parameter: one draw per firing) and an optional
`window` (`start_minute` and `end_minute`, 0–1439 in host local time, start before end, both bounds
inclusive). Without a window, every minute of the day is in range. The default policy is
`interval{every_minutes: 30, probability: 0.25}`. A channel's resolved policy must hold exactly one
`interval` rule.

```yaml
modules:
  timer:
    channels:
      - destination:
          platform: twitch
          channel_id: ${TWITCH_BROADCASTER_ID}
        prompt_text: "Say something about what is on screen right now."

triggers:
  timer:
    channels:
      ${TWITCH_BROADCASTER_ID}:
        combination: all_of
        rules:
          - type: interval
            parameters:
              every_minutes: 20
              probability: 0.5
              window:
                start_minute: 1080
                end_minute: 1380
```

Here a firing is due every 20 minutes; between 18:00 and 23:00 each one becomes a candidate with a
probability of one half.

## Out of scope: continuous listening

Continuous listening is out of scope. The companion never transcribes on a schedule or continuously,
never streams audio continuously to a transcriber or to the core, and never listens by default. The
cost of listening is bounded by the wake word, not by time: one detector verdict, then at most one
acquisition and one transcription per accepted candidate. Matching the full transcription of every
utterance before deciding would mean transcribing everything that is said. A future plugin may offer
that, deliberately opt-in and deliberately expensive, without changing the contract above. Audio is
stored only when a run calls `audio.capture`, exactly as before.

## Manual real-instance trial

The suite proves each family against fakes only. The real-instance check is a manual procedure an
operator runs on their own channel, and its record is `docs/campaigns/phase8/trial-record.md` (status
`not run` until an operator runs it). No test performs any of these steps.

1. Start from a profile that works for chat. Add the four fragments above, adapted to your channel,
   then validate the profile without opening a socket:

   ```bash
   python -m core.main --config my-config.yaml --check-config
   ```

2. Run the companion with `python -m core.main --config my-config.yaml` and keep its trace output.
3. **Channel points.** Grant the redemption-read scope, create a reward whose id you configured, and
   redeem it from a viewer account. Then redeem a reward whose id you did not configure.
4. **Button.** Press each configured button from the page, from the overlay loaded as a browser
   source, and from the key bound to the press command. Press one again within its cooldown.
5. **Voice.** Say the wake phrase, then a phrase the `keyword` rule does not list, then stop the
   detector program and restart it.
6. **Timer.** Wait for two firings inside the window and one outside it.
7. For each family, write into the trial record: the date, the version (commit) and platform, what
   was triggered, an excerpt of the decision trace (the reason, never a token or a key), and the
   outcome — whether the run happened, was refused, or nothing was decided. Set the record's status
   when every family has been tried.
