# The first run — from the launcher to a first answer

**English** · [Français](first-run.fr.md)

This guide is for a streamer on their own PC, once the companion is installed. It walks through the
first fifteen minutes: start the companion from its launcher, pass the five setup screens, and watch
it work in the console. Nothing in it needs a terminal or a YAML file. The English text is normative;
the French translation follows it.

Where this guide and the Phase 9 specification (`docs/campaigns/phase9/spec.md`) differ, the
specification wins.

## 1. Before you start: the install

The install comes **before** the fifteen minutes, and it may be done by someone else for you (a friend,
the operator). It is the one part that still uses a terminal:

1. Get Python and the project, and install it, exactly as [`docs/installation.md`](installation.md)
   describes in its sections 2 and 4 (the shopping list and *Installing the project*). Sections 5 to 9
   of that guide — gathering values, writing a YAML file, setting environment variables — are **not**
   needed for the first run: the setup screens below replace them.
2. In the same terminal, with the environment still active, write the launcher:

```sh
python -m core.config_ui --install-launcher
```

The command writes one launcher for your account and prints its path (`Launcher: …`), then exits. It
starts nothing. The launcher runs the very Python the project was installed in, with
`python -m core.config_ui` and no other argument, so it always opens the default profile below.

| Platform | Launcher written | Where you find it |
|---|---|---|
| Linux | `twitch-ia-compagnon.desktop` in `~/.local/share/applications/` | your applications menu |
| Windows | `twitch-ia-compagnon.cmd` in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\` | the Start Menu |
| macOS | `twitch-ia-compagnon.command` in `~/Applications/` | the Applications folder of your home folder |

Running `--install-launcher` again replaces a launcher it wrote; a file at that place that it did not
write is left unchanged, and the command says so. The launcher never holds a token or a password.

From here on, no terminal is used.

## 2. Starting from the launcher

Click the launcher like any application. The companion opens your default browser on its page by
itself; the address carries a one-time access token and only works on this computer
(`127.0.0.1`). The first time, the page is the setup (the *wizard*); once setup is finished, the same
click opens the console.

Clicking the launcher again while the companion is already open does not start a second one: it opens
the page of the one already running. If another version is running, nothing is opened and the page
of the running one is named instead. If the previous launch did not stop cleanly, the page says what
it found and what to do; it never silently deletes a record of a companion that may still be starting.

On Windows, a second click cannot reopen the page: the record of the running companion does not keep
its access token there, so the new launch cannot confirm the running one and opens nothing. It says
which process holds the record and on which port the companion listens. Go back to the browser tab
the first launch opened (the page is on `127.0.0.1` at that port); if you closed it, close the program
holding that process, or restart the computer, and click the launcher again, which opens a fresh page.

## 3. Where your files live

Started from the launcher, the companion uses one profile in your account's own folder. "First run"
means: this file does not exist yet.

| Platform | Default profile |
|---|---|
| Linux | `$XDG_CONFIG_HOME/twitch-ia-compagnon/profile.yaml` (`~/.config/twitch-ia-compagnon/profile.yaml` when `XDG_CONFIG_HOME` is not set) |
| Windows | `%APPDATA%\twitch-ia-compagnon\profile.yaml` |
| macOS | `~/Library/Application Support/twitch-ia-compagnon/profile.yaml` |

Beside the profile, the companion keeps its own small files: the setup progress
(`profile.yaml.wizard.json`), the record of the running window (`profile.yaml.instance.json`), the
runtime status (`profile.yaml.status.json`), the console's feed (`profile.yaml.feed.jsonl`) and, once
you use the editor, your changes (`profile.local.yaml`). None of them holds your keys or tokens, with
one exception: on Linux and macOS, the record of the running window holds the page's address with its
access token, so that a second click can reopen the page. That file is readable by you only and is
removed when the companion stops cleanly; while it exists, treat it like a password and do not share it.

### The credential file

The keys and tokens you enter during setup are **not** in the profile. The profile only names them
(`${TWITCH_ACCESS_TOKEN}` and the like). Their values are kept in one credential file, named after the
profile's file name with `.credentials.json` appended — `<profile-file-name>.credentials.json`, so
`profile.yaml.credentials.json` for the default profile. Two profiles never share one: `a.yaml` and
`a.yml` have two files.

The credential file lives in a per-user folder that only your account can open:

| Platform | Credential folder | Protection |
|---|---|---|
| Linux | `$XDG_CONFIG_HOME/twitch-ia-compagnon/` (`~/.config/twitch-ia-compagnon/`) | the folder is yours only (`0700`), the file is readable by you only (`0600`) |
| Windows | `%LOCALAPPDATA%\twitch-ia-compagnon\` | your account's local application data, which Windows keeps to your account |
| macOS | `~/Library/Application Support/twitch-ia-compagnon/` | the folder is yours only (`0700`), the file is readable by you only (`0600`) |

Screen 4 names the folder it used and why. Where the computer gives no such protection — on Linux or
macOS a folder that belongs to another account or that other accounts can write to, on Windows no
`%LOCALAPPDATA%` folder — the companion stores your credentials **nowhere**, says so, and screen 4
stays closed until the folder is fixed.

The values are never shown again: not on a page, not in a log, not in the console, not in the
read-only profile view. A field you already filled is shown empty and marked *kept*.

**Only the companion's own window reads that file.** The console's **Start** gives the stored values
to the runtime it launches. A runtime started from a terminal (`python -m core.main --config …`) does
**not** read the credential file: it reads environment variables only, as
[`docs/installation.md`](installation.md) section 7 describes.

## 4. The five setup screens

Each screen opens only once the previous one has **really** succeeded: the check is made by the
companion, not by a box you tick. If something fails, the screen stays open and shows one sentence
saying what went wrong and what to do next; a *Show the raw detail* control, closed by default, holds
the technical detail when there is one. Changing something on a screen you already passed reopens that
screen and every one after it. Closing the window halfway is safe: the next click on the launcher
resumes the setup.

1. **Language and profile.** Pick English or French (you can change it on any page), then one of the
   four ready-made profiles, and fill in what it asks:
   - **Chat Q&A** — answers a chat message that mentions the companion's name (asks for that name);
   - **Channel points** — reacts when a viewer redeems one custom reward you choose (the reward is
     picked on screen 2);
   - **Spoken keyword** — listens to your microphone and answers when you say the wake word (asks for
     the wake word, the recording command, the detector and the transcription settings);
   - **Silent** — connects to your channel's chat but never answers: every trigger is off.
2. **Your Twitch account.** Enter the client id and client secret of your Twitch application and the
   access token of the account the companion speaks as. Twitch itself checks them: the token must be
   valid, belong to that application and carry the permissions the profile needs. The screen then
   shows the channel the token names; you never type numeric identifiers. For *Channel points*, the
   channel's custom rewards are listed and you pick one.

   **Where the three values come from.** The setup cannot make them for you (see
   [What is not proven](#7-what-is-not-proven)); you make them once, on Twitch's own site and with
   Twitch's own tool, logged in as the account the companion speaks as:
   1. Open [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) and log in. Twitch asks
      you to turn on two-factor authentication first if it is off.
   2. Choose **Register Your Application**: any name, OAuth Redirect URL `http://localhost:3000`,
      category *Chat Bot*, client type *Confidential*. Save, then **Manage**.
   3. Copy the **Client ID** into *client id*. Choose **New Secret** and copy it into *client secret*
      at once: Twitch shows it only once.
   4. Install the official [Twitch CLI](https://dev.twitch.tv/docs/cli/), run `twitch configure` and
      give it the same client id and secret, then run `twitch token -u -s "user:read:chat user:write:chat"`
      (for *Channel points* add `channel:read:redemptions` inside the quotes; for *Silent*
      `user:read:chat` alone is enough). A browser page asks you to authorise your application; the
      command then prints a **User Access Token**: that is the *access token*. It must come from your
      own application: a token made by a third-party generator belongs to that site's application,
      and the screen refuses it.
   5. The token expires; when Twitch refuses it, repeat step 4 and enter the new one on this screen.
3. **The model.** Enter the model's address, its name and its API key. The companion sends one real
   request and passes the screen only when the model answers with text. For *Silent*, this screen is
   marked *not needed*.
4. **Your profile.** *Write the profile and check it* writes the profile at the default place, stores
   your credentials in the credential file, and runs the same configuration check as
   `--check-config`. *Show the profile (read-only)* displays what was written — you never have to edit
   it. The setup only ever writes files it wrote itself: a profile that already existed, or that was
   changed since by someone else, is left untouched and the screen points you to the editor.
5. **A dry run.** *Start the dry run* sends one pretend event of your profile's kind through it.
   Nothing is posted to your channel. The screen passes only when the dry run records a run, and shows
   it: the run id and its outcome. For *Silent*, the outcome is "nothing was triggered", as intended.
   The setup is then finished; *Open the console* takes you there.

The setup never starts the companion on your channel: that is the console's **Start**.

## 5. The console: is it working?

The console answers one question, *is it working?*, in plain sentences in your language:

- **State** — running from this window, not running, or another companion process that this window
  did not start and does not control;
- **Modules** — each enabled module, ready or degraded; for a degraded one, its cause and what to do;
- **What just happened** — the feed, newest first and refreshed by itself: a viewer's message arriving,
  a trigger accepting it or letting it pass, an answer queued, started and finished, an action refused,
  each with the reason in a sentence;
- **Start** and **Stop** — **Start** launches the companion with your profile and your stored
  credentials, only when no other companion holds this profile; **Stop** stops only the one this window
  started. Both say what happened. If the companion stops while starting, the console shows the cause
  and the next action, and the program's own last lines behind *Show the raw detail*, closed by default
  and with every secret removed;
- **What the companion is allowed to do** — the configured grants (rule, action, who, where), each with
  a **Revoke** button. Revoking removes the grant and, if the companion runs from this window, restarts
  it without it. The console can only take permissions away: nothing on it adds or widens one.

## 6. Moving on to the editor

When the ready-made profile is no longer enough, *Open the editor* (on the console) opens the existing
configuration editor on the same profile. It is the operator's tool, described in
[`docs/config-ui.md`](config-ui.md): every module's settings, *Check*, *Apply*. It never rewrites your
profile; your changes go to `profile.local.yaml` beside it, laid over the profile, and removing a
change there restores the profile's value. Secrets and permissions stay read-only there; the
credential file is still the one the console uses.

Once the setup is finished, it is not offered again for this profile: further changes belong to the
editor. An operator who prefers files can also start the same page on any
profile with `--config`, as [`docs/config-ui.md`](config-ui.md) describes; it then opens on the
editor.

## 7. What is not proven

The fifteen-minute claim — from a working install to a first run in under fifteen minutes, without a
terminal and without YAML — rests only on the recorded trial (`docs/campaigns/phase9/trial-record.md`),
and holds only for the platform that record names. The install itself still needs a terminal; that
step is outside the fifteen minutes and is listed among the record's limits.

Screen 2 is a limit of the setup as it stands: it asks for a client id, a client secret and an access
token, and nothing in the setup makes them. A streamer who has never registered a Twitch application
has to follow the five steps under screen 2 — on Twitch's developer site and with Twitch's command-line
tool — before the setup can go on. No sign-in-with-Twitch flow exists yet.
