# Phase 8 — real-instance trial record

Status: not run

This is the record of the manual real-instance trial described in
[`docs/triggers.md`, "Manual real-instance trial"](../../triggers.md#manual-real-instance-trial).
No test performs any of its steps: the suite proves each family against fakes only. An operator
fills the fields below on their own channel and sets the status (`run`, or `partial` with the
families not tried named) once every family has been tried.

Never paste a token, a key, a user access token or a reward secret here. A decision trace excerpt
is the decision's reason, its input, its policy version and, when present, its `draws` — nothing
else.

## Run

- Date:
- Operator:
- Version (commit):
- Platform (OS, Python version):
- Profile used (path, not contents):
- `--check-config` result:

## Channel points (`reward`)

- What was triggered (a configured reward id, then an unconfigured one):
- Decision trace excerpt:
- Outcome (run happened / refused / nothing decided):

## Button (`operator`, `button`)

- What was triggered (page, overlay as a browser source, bound key; one press within its cooldown):
- Decision trace excerpt:
- Outcome (run happened / refused / nothing decided):

## Voice (`audio_input`, `wake_word` and `keyword`)

- What was triggered (the wake phrase, a phrase the `keyword` rule does not list, detector stopped
  and restarted):
- Decision trace excerpt:
- Outcome (run happened / refused / nothing decided):

## Timer (`timer`, `interval`)

- What was triggered (two firings inside the window, one outside it):
- Decision trace excerpt:
- Outcome (run happened / refused / nothing decided):

## Notes

- Deviations from the procedure:
- Anything that behaved differently from the fakes:
