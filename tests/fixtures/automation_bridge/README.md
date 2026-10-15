# Automation-bridge fixtures

Phase 5, plan step P13 (R8, AC35). Every frame the fake-instance tests replay
lives in one of these files. A test never invents an instance frame: it picks
one from here (the only exception is the deliberately malformed hello, a fault
injection of `FakeInstance`, which claims nothing about the instance).

## Format

```json
{"frames": [{"provenance": {...}, "question": 1, "frame": {...}}]}
```

- `provenance` is required:
  - `{"kind": "documented", "ledger_id": <int>}` — an id of
    `docs/research/sources-ledger-streamerbot.json`;
  - `{"kind": "observed", "trial_record": "docs/campaigns/phase5/bridge-trial-record.md#qN"}`.
- `question` (optional, 1..8) names the section 9 question of
  `docs/research/streamerbot-etude-2026-10.md` the frame exercises. A frame
  naming a question must be `observed`. Until the trial record exists, **no
  frame names a question** (the guard in `tests/test_automation_bridge.py`
  asserts the count is 0). In particular there is no frame answering a
  request that names a nonexistent action (question 1) and none for a
  custom-event round trip (question 6).
- `frame` is the JSON object the instance sends. Only server-to-client frames
  are stored. A response frame carries `"id": "request-1"`; `FakeInstance`
  replaces it with the id of the request it answers (and, for `Subscribe`,
  echoes the requested `events`, as the documented response does).

## Ledger ids used

| id | page | frames |
| --- | --- | --- |
| 2 | WebSocket Requests | `GetActions`, `DoAction` (ok and error), `Subscribe`, `GetActiveViewers` responses |
| 4 | Authentication guide | the hello with and without `authentication` |
| 5 | Sending requests (base response `{"status", "id"}`) | the `Authenticate` answer, the generic error answer |
| 6 | WebSocket Events (index of 45 categories) | every event frame |

The per-event schemas are on the pages the events index (id 6) links, e.g.
`/api/websocket/events/twitch/chat-message`; the event envelope
(`timeStamp`, `event.source`, `event.type`, `data`) is the example of the
events guide (`/api/websocket/guide/events`). Neither has its own ledger id;
both are cited under id 6.

## Files and frame order

| file | frames, in order |
| --- | --- |
| `hello.json` | hello without authentication; hello with `authentication.{salt, challenge}` |
| `authentication.json` | `Authenticate` accepted |
| `error_response.json` | the generic error answer (`{"status": "error", "id"}`) |
| `get_actions.json` | an inventory `A` (group `Companion`), `B` (`Other`), `C` twice (`Other`), `D` disabled (`Companion`); an empty inventory |
| `do_action.json` | the acknowledgement; the error answer |
| `subscribe.json` | the subscription answer |
| `custom_event.json` | one `Custom.Event` broadcast |
| `active_viewers.json` | an active-viewer list across the three platforms |
| `chat_twitch.json` | `Twitch.ChatMessage` (author has role 3 and a `moderator` badge) |
| `chat_kick.json` | `Kick.ChatMessage` |
| `chat_youtube.json` | `YouTube.Message` |
| `notices_twitch.json` | `Sub`, `ReSub`, `GiftSub`, `GiftBomb`, `Raid`, `Follow` |
| `notices_kick.json` | `Follow`, `Subscription`, `Resubscription`, `GiftSubscription` |
| `notices_youtube.json` | `NewSponsor`, `MemberMileStone`, `MembershipGift`, `SuperChat`, `SuperSticker` |

## Enumerated notice kinds (the AC43 list)

The events index (id 6) documents 139 Twitch, 21 Kick and 31 YouTube event
types. The notice fixtures are the documented event types that carry a
community notice the native platform module also produces; **this set is the
list** AC43 counts.

| platform | instance event | native kind |
| --- | --- | --- |
| twitch | `Sub` | `sub` |
| twitch | `ReSub` | `resub` |
| twitch | `GiftSub` | `sub_gift` |
| twitch | `GiftBomb` | `community_sub_gift` |
| twitch | `Raid` | `raid` |
| twitch | `Follow` | `follow` |
| kick | `Subscription` | `sub` |
| kick | `Resubscription` | `resub` |
| kick | `GiftSubscription` | `sub_gift` |
| kick | `Follow` | `follow` |
| youtube | `NewSponsor` | `sub` |
| youtube | `MemberMileStone` | `resub` |
| youtube | `MembershipGift` | `sub_gift` |
| youtube | `SuperChat` | `tip` |
| youtube | `SuperSticker` | `tip` |

Documented types left out on purpose: those with no native notice kind
(`Cheer`, `HypeChat`, `Kick.MassGiftSubscription`, `KicksGifted`,
`YouTube.GiftMembershipReceived`, `JewelsGifted`, …). An instance event with
no mapping is dropped and counted (R7b).

## Ambiguities for the trial

The documented example payloads are generated filler (random strings, `null`
users), so the fixtures fill the **documented schema's field names and types**
with plausible values rather than copy the filler verbatim. Points the trial
must confirm:

1. **Envelope timestamp key.** The events guide example writes `timeStamp`;
   the hello writes `timestamp`. The fixtures follow each page.
2. **`ViewerRole` values.** Every user schema types `role` as an integer enum
   0–4 without naming the values. The Twitch chat fixture uses `3` for a
   moderator (and a `moderator` badge); the mapping must not assume more.
   `GetActiveViewers` types `role` as a string (`"Viewer"`, `"Moderator"` here).
3. **Platform spelling.** The event `source` is capitalised (`Twitch`, `Kick`,
   `YouTube`); `user.type` and the active viewer's `type` are written in
   lower case here, the documented example giving only `<platform>`.
4. **Error answers.** The requests pages document no error body beyond
   `{"status": "error", "id"}`; no fixture carries an `error` message, and the
   bridge must not depend on one. A refused `Authenticate` is documented by no
   page (question 3).
5. **`Twitch.Follow`** has `broadcaster` and `targetUser` and no `user`; the
   fixture puts the follower in `targetUser`.
6. **No message id.** `Twitch.Raid`, `Twitch.Follow` and every Kick notice
   carry no message or event id (`Kick.Follow` carries only `user` and
   `timestamp`); YouTube events carry `eventId`.
7. **`Kick.ChatMessage`'s** schema is titled `KickFirstWordEvent` on its page.
8. **`Twitch.GiftSub`** spells `cumlativeTotal` as documented.
9. **Active viewers.** `GetActiveViewers` takes no documented platform
   parameter; each viewer carries its platform in `type`.
