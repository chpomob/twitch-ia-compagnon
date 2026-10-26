# Installing and running the companion

**English** · [Français](installation.fr.md)

> **Read this first.** This project is an **architectural exploration**, published as it is built. It
> has **never run on a real stream**: its connections to Twitch, Kick and YouTube have never talked to
> the live services, and there is no field experience of any kind. So this guide does not install a
> finished product — it installs a project that works, of which you will be the first real user. There
> is no automatic update, no installer, no service that restarts it for you, and nobody who will fix a
> bug on your behalf. If what you want is a dependable tool today, this is not it yet: read
> [What is not proven](#13-what-is-not-proven) at the end.

This guide takes you from "I have installed nothing" to "my assistant answers in my chat". Budget
**30 minutes**, half of it spent creating accounts on the web.

## 1. Who this guide is for

You **do not need to know how to program**. You do need to accept three things:

- opening a black window called a "terminal" and **copying and pasting** commands into it;
- creating two accounts on the web (Twitch developer, and a language-model provider);
- copying values from a web page into a file without mistyping them.

Everything else is copying and pasting. Every command is given **exactly as you should type it**,
followed by what you must see next. If what you see is something else, jump to
[12. When something goes wrong](#12-when-something-goes-wrong).

A few words, used throughout:

- **terminal**: the window where commands are typed (PowerShell or Command Prompt on Windows,
  Terminal on macOS, any terminal on Linux);
- **companion**: your assistant, the one answering in the chat;
- **profile**: the configuration file (`mon-compagnon.yaml` in this guide);
- **model**: the AI that writes the answers (ChatGPT, Claude, Mistral, a local model…).

## 2. The shopping list

Prepare these five things **in this order**. The "where" column is a direct link.

| # | What you need | Why | Where / Cost |
|---|---|---|---|
| 1 | **Python 3.10 or newer** | the program is written in Python | [python.org/downloads](https://www.python.org/downloads/) — free. On Windows, **tick "Add python.exe to PATH"** during installation, or nothing will work |
| 2 | **A Twitch account + a developer application** | so the companion can read and write in your chat | [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) → "Register Your Application". You get a **client id** and a **client secret** out of it. Free |
| 3 | **A Twitch access token + two numeric identifiers** | the token is the bot's password; the two identifiers say *which channel* and *which bot account* | [twitchtokengenerator.com](https://twitchtokengenerator.com) (third-party site, not affiliated) or the official `twitch` CLI. Untick everything except `chat:read` and `chat:edit`. Free. **The token expires: you will have to make a new one from time to time** |
| 4 | **A language-model key** | it is the "brain" that writes the answers | [OpenAI](https://platform.openai.com/) (paid by usage), [Groq](https://console.groq.com/), [DeepInfra](https://deepinfra.com/), [Mistral](https://console.mistral.ai/)… or a **local** model with [Ollama](https://ollama.com/) (free, on your own PC). **The model must support "tool calling" (function calling)**: without it, the companion will start and then be unable to do anything |
| 5 | **The project folder** | the software itself | the project's GitHub page → green **Code** button → **Download ZIP**, then unzip it (or `git clone` if you know git) |

What it costs: **€0** for Twitch and Python; **a few cents to a few euros per evening** for the model,
billed by usage. A local model costs nothing but uses your PC.

## 3. First: see what it looks like (2 minutes, no account)

Before installing anything, know what you are working on. In the project folder, in a terminal:

```sh
python -m demo
```

What you must see: a small story that plays itself — a viewer writes, the companion hears it, thinks,
captures audio, answers in the chat and speaks the answer out loud. It is an **offline** demonstration:
it opens no connection, uses no account and costs nothing. It is how you check that your installation
is alive — the GIF in the repository shows exactly this output.

## 4. Installing the project

### 4.1 Open a terminal **inside the project folder**

This is where people trip most often. The terminal must be *inside* the unzipped folder, otherwise the
commands find nothing.

- **Windows**: open the project folder in File Explorer, click the address bar, type `powershell`,
  press Enter.
- **macOS**: right-click the folder → Services → "New Terminal at Folder".
- **Linux**: right-click inside the folder → "Open in Terminal".

### 4.2 Install

**Windows** (one line at a time):

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
```

**macOS and Linux** (one line at a time):

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

What you must see: the last command ends with `Successfully installed …`, and your prompt now starts
with `(.venv)`. That is the sign the environment is active.

> **Remember:** `(.venv)` must be visible for every command below. The terminal forgets the environment
> when you close it: reactivate it (the 2nd line above) in every new session.

## 5. Gathering the eight values

Go back to the shopping list and fill in this table **in a scratch file**. This is the tedious part, and
a typo here is the first cause of failure.

| Value | What it is | Where to get it |
|---|---|---|
| `TWITCH_CLIENT_ID` | your Twitch application's public identifier | the Twitch developer site, your application's page |
| `TWITCH_CLIENT_SECRET` | your Twitch application's password | same page, "New Secret" button |
| `TWITCH_ACCESS_TOKEN` | the bot's password | the token generator (`chat:read` + `chat:edit` scopes) |
| `TWITCH_BROADCASTER_ID` | the **numeric** identifier of your channel (not your username) | the token generator shows it; otherwise a "Twitch user id lookup" tool |
| `TWITCH_BOT_USER_ID` | the numeric identifier of the account that speaks (the bot) | same method, with the bot account |
| `MODEL_ENDPOINT` | the complete Chat Completions address of the model service, ending in `/chat/completions` | OpenAI: `https://api.openai.com/v1/chat/completions` · local Ollama: `http://localhost:11434/v1/chat/completions` · otherwise the address your provider gives you, with `/chat/completions` added if it stops at `/v1` |
| `MODEL_NAME` | the model's exact name | from your provider (e.g. `gpt-4o-mini`, `llama-3.3-70b-versatile`) |
| `MODEL_API_KEY` | your model key | your provider's "API keys" page (local Ollama: anything, `ollama` for instance) |

> The **bot** account can be your own account, but a second Twitch account is cleaner: the companion's
> messages then do not appear as yours.

## 6. Creating the configuration file

At the root of the project (the folder that also holds `README.md`), create a file named
**`mon-compagnon.yaml`** and paste exactly this into it:

```yaml
modules_directory: ./modules

enabled_modules:
  - twitch
  - chat_context
  - brain
  - audit

modules:
  twitch:
    client_id: ${TWITCH_CLIENT_ID}
    client_secret: ${TWITCH_CLIENT_SECRET}
    access_token: ${TWITCH_ACCESS_TOKEN}
    broadcaster_id: ${TWITCH_BROADCASTER_ID}
    bot_user_id: ${TWITCH_BOT_USER_ID}
    # The name viewers use to call the companion.
    # CHANGE IT (and keep it short): this is the word that triggers an answer.
    companion_name: Assistant

  # The companion keeps the last chat messages in memory.
  chat_context: {}

  brain:
    endpoint: ${MODEL_ENDPOINT}
    model: ${MODEL_NAME}
    api_key: ${MODEL_API_KEY}
    admission:
      session_queue_capacity: 4
      global_pending_capacity: 64
      max_sessions: 32
      workers: 4
      wait_seconds: 30
      total_run_seconds: 60
    budget:
      model_turns: 5
      model_call_seconds: 30
      action_seconds: 10
      max_tokens: 8192
      max_observation_bytes: 5242880
      max_action_calls: 6
      max_repeated_actions: 2
      delivery_reserve_seconds: 10
    conversation_memory:
      max_sessions: 64
      max_exchanges: 20
      max_bytes: 65536
      max_age_seconds: 3600
    fallback:
      enabled: true
      text: "I could not answer in time."
    capabilities:
      required:
        - structured_output
    delivery:
      mode: fixed
      actions:
        - action: chat.write
          text_argument: text

  audit:
    output: stdout
    queue:
      max_records: 1000
      max_bytes: 4194304

secrets:
  - ${TWITCH_CLIENT_SECRET}
  - ${TWITCH_ACCESS_TOKEN}
  - ${MODEL_API_KEY}
  - ${MODEL_ENDPOINT}

actions:
  - rule_id: brain-reads-chat
    action_name: chat.read
    destination:
      platform: twitch
      channel_id: ${TWITCH_BROADCASTER_ID}
      scope: chat
    principals: [brain]
    natures: [read]
    granted_permissions: [chat.read]
  - rule_id: brain-delivers-chat
    action_name: chat.write
    destination:
      platform: twitch
      channel_id: ${TWITCH_BROADCASTER_ID}
      scope: chat
    principals: [brain]
    natures: [write]
    granted_permissions: [chat.write]

limits:
  bus_history:
    max_events: 1000
    max_bytes: 4194304
    max_age_seconds: 600
  observation_queue:
    max_records: 1000
    max_bytes: 4194304
  dedup:
    max_entries: 4096
    ttl_seconds: 600
  attachments:
    max_object_bytes: 1048576
    max_objects: 256
    max_total_bytes: 67108864
    max_bytes_per_run: 8388608
    ttl_seconds: 900
  conversation_memory:
    max_sessions: 64
    max_exchanges: 20
    max_bytes: 65536
    max_age_seconds: 3600
  chat_context:
    max_messages: 50
    max_bytes: 65536
    max_age_seconds: 900
    max_channels: 8
  admission:
    session_queue_capacity: 4
    global_pending_capacity: 64
    max_sessions: 32
    workers: 4
    wait_seconds: 30
    total_run_seconds: 60
```

You have **nothing to change** in this file, except `companion_name` (the companion's name). The
`${...}` are references to the previous section's variables: the program substitutes your values by
itself, and never writes them into the file.

In three sentences, what this profile says:

- `enabled_modules`: the four parts that are switched on — the Twitch connection, the chat memory, the
  brain, the log;
- `actions`: **what the companion is allowed to do**, and nothing else. Two permissions here: read the
  chat, write to it. Outside this list, everything is refused — it is the most important setting in the
  file, and the easiest one to extend later;
- `limits` and the `admission`/`budget` blocks: the guard rails (how much memory, how many pending
  answers, how long an answer may take). The values given are sane: leave them alone to start with.

## 7. Making the eight values visible to the program

The program reads your values from the **terminal**, not from a file. Type the eight lines below in the
same terminal where `(.venv)` is visible — replacing each value with your own, **without quotes**:

**Windows — PowerShell:**

```powershell
$env:TWITCH_CLIENT_ID="paste_here"
$env:TWITCH_CLIENT_SECRET="paste_here"
$env:TWITCH_ACCESS_TOKEN="paste_here"
$env:TWITCH_BROADCASTER_ID="paste_here"
$env:TWITCH_BOT_USER_ID="paste_here"
$env:MODEL_ENDPOINT="paste_here"
$env:MODEL_NAME="paste_here"
$env:MODEL_API_KEY="paste_here"
```

**Windows — Command Prompt** (`cmd`): the same list in the form `set TWITCH_CLIENT_ID=paste_here`,
with no quotes and no spaces around the `=`.

**macOS and Linux:**

```sh
export TWITCH_CLIENT_ID=paste_here
export TWITCH_CLIENT_SECRET=paste_here
export TWITCH_ACCESS_TOKEN=paste_here
export TWITCH_BROADCASTER_ID=paste_here
export TWITCH_BOT_USER_ID=paste_here
export MODEL_ENDPOINT=paste_here
export MODEL_NAME=paste_here
export MODEL_API_KEY=paste_here
```

These values do not survive closing the terminal. That is normal, and even desirable: the day retyping
them annoys you enough, it is the moment to read [`docs/config-ui.md`](config-ui.md) and keep this guide
within reach.

## 8. Checking without switching anything on

Before running anything live, ask the program to check your profile. It connects to nothing and reads no
message:

```sh
python -m core.main --config mon-compagnon.yaml --check-config
```

What you must see: **nothing at all**, and an immediate return to the prompt. That is the good news:
`nothing` means "everything is consistent". If there is an error message, it names the field that is
wrong — see section [12](#12-when-something-goes-wrong).

## 9. Running the companion

```sh
python -m core.main --config mon-compagnon.yaml
```

What you must see: four `ready` lines (the four parts starting up), then the program stays silent — it
is listening. There is **nothing to type**: go to your Twitch chat and address the companion by name:

```
Assistant, are you there?
```

What you must see next: in the chat, an answer written by the bot account; in the terminal, a series of
lines (`chat`, `trigger`, `call`, `run`) telling the story of what it just did. The `audit` lines are
your log: they say exactly what was decided.

To stop it: `Ctrl` + `C` in the terminal.

> Test in a **private chat with yourself** first, or on a test channel — not in front of your audience.

## 10. The web configuration interface

Remembering eight variables and editing YAML is not sustainable day to day. The project ships a small
local web interface to read and change your configuration:

```sh
python -m core.config_ui --config mon-compagnon.yaml
```

What you must see: a one-time address along the lines of
`Configuration UI: http://127.0.0.1:8765/?token=…` — copy it into your browser. It only works on your
own machine (it is not exposed on the internet) and the token changes on every launch.

Three things to know about this interface:

- it writes your changes into a **separate file** (`mon-compagnon.local.yaml`, next to it) laid over your profile: your
  `mon-compagnon.yaml` stays intact, and removing a line in the interface restores the original value;
- **secrets** and the **authorization rules** (the `actions` block) are read-only there, on purpose: a
  stray click must not be able to widen what the companion is allowed to do;
- applying a change needs a **manual restart**: stop it (`Ctrl` + `C`) and run the command from section
  9 again.

> The details of that interface are in the operator guide [`docs/config-ui.md`](config-ui.md), also
> available [in French](config-ui.fr.md). One honesty: the interface's browser layer has never been
> tried in a real browser (its HTTP contract is tested, its rendering is not). If a button seems
> dead, that is probably where.

## 11. Adding voice, OBS, screen captures

The profile from section 6 deliberately does the minimum: it reads the chat and answers in it.
Everything else is **modular** — each capability is a part you switch on, with its own settings. You do
not have to compose them from scratch: the complete profiles shipped with the project are your models.

| What you want | Part to enable | What you also need, outside the project |
|---|---|---|
| hear the companion in the stream | `audio_output` | a speech-synthesis service (endpoint + key), an audio player installed |
| have the companion hear you | `audio_input` | a microphone, a command-line recorder, a transcription service |
| change your scenes, your alerts | `stream_control` | OBS with `obs-websocket` enabled |
| talk about a screen capture | `capture` | a command-line screen-capture tool |
| recognise regulars | `users`, `viewer_memory` | nothing |
| moderate, clip a moment, run a poll | `moderation`, `clips`, `twitch` | extra permissions on your Twitch token |
| split the brain from the streaming PC | `proxy`, `agent_link` | a second computer or a server ([protocol](proxy-protocol.md)) |

Start from `config.yaml.example` (the comment above each line explains the setting) and
`presence.yaml.example` (the full profile: persona, memory, routes). Copy the blocks you want into your
file. After each addition, redo the check from section 8 before restarting: the error message will name
the setting you are missing.

## 12. When something goes wrong

The messages below are the ones the program actually prints. The heading of each row is what you see on
screen.

| What you see | What it means | What to do |
|---|---|---|
| `environment reference is unresolved` | one `${...}` in your profile matches no variable in your terminal | check section 7: the eight values must be defined **in this terminal** (the terminal forgets everything when you close it) |
| `twitch authentication: rejected (status 401)` | Twitch refused your token | your `TWITCH_ACCESS_TOKEN` is wrong, mistyped or expired: make a new one, and check that `TWITCH_CLIENT_ID` belongs to the same application |
| `error: module 'brain': phase 'prepare' failed` | the brain could not reach the model, or got no usable answer | check `MODEL_ENDPOINT` (a trailing slash is enough to break the address), `MODEL_NAME` (exact spelling) and `MODEL_API_KEY`, then that the model **supports tool calling**. Be warned: today this message **does not say** which of those it is — that is a known limitation of the project |
| `secrets[N]: is referenced by no setting` | you listed in `secrets:` a variable that nothing uses | delete that line from the `secrets` block |
| `environment reference is unresolved` on a shipped `.example` file | normal: those examples name example variables | check **your** file, not the `.example` ones |
| The companion starts but never answers | it is not being called, or it is not allowed to answer | write its name exactly as in `companion_name`; check your token carries both `chat:read` and `chat:edit`; check the bot account has access to your channel |
| It answers "late", or stops answering after a while | the Twitch token expired (about 60 days) | make a new token, restart |
| The answer is empty or truncated | either the model "thinks" and spends its whole budget, or the length limit is too low | raise `brain.budget.max_tokens` in the profile |
| A billing error from your provider | your model key is billed by usage | nothing to do with the project: watch your account, or switch to a local model |

## 13. What is not proven

Keep this in mind before going live:

- **no real stream has ever run with this software**;
- **the connections to Twitch, Kick and YouTube have never talked to the live services**: they are
  tested against scripted servers. Authentication, rate limits and the particularities of those services
  are unverified;
- **the web interface has never been tried in a real browser**;
- **no production hardening**: no system-wide install, no upgrades, no supervision, no load testing;
- the project is released under **WTFPL**: use it, modify it, redistribute it freely, and with
  **no warranty and no support of any kind**.

Concretely, for a live evening: start in a private chat or on a test channel, keep moderation in your
hands, and be ready to cut it (`Ctrl` + `C`) if the companion goes somewhere you do not like.

## 14. Going further

| Document | Content |
|---|---|
| `README.md` | the overview, what is proven and what is not, and the offline demo |
| [`docs/config-ui.md`](config-ui.md) | the guide to the web interface (also [in French](config-ui.fr.md)) |
| [`docs/proxy-protocol.md`](proxy-protocol.md) | running the brain on another computer (normative; also [in French](proxy-protocol.fr.md), courtesy translation) |
| [`docs/design-v2.md`](design-v2.md) | the overall design: why the parts are modular (also [in French](design-v2.fr.md)) |
| `config.yaml.example`, `presence.yaml.example` | the complete profiles, commented line by line |
| `python -m demo --platform fake` | the same demonstration on a second platform, to see that the core depends on none in particular |

To run the companion on a server — a VPS, a NAS, a machine without a desktop — with Docker and no copy
of the project, follow the self-hosting guide [`docs/self-hosting.md`](self-hosting.md) instead of this
one: one compose file takes the place of the install, and the setup screens the place of the values and
the YAML.
