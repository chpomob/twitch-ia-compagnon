# Step P27F1 — gate findings F1 and F4 (community notices reach the routes; no fictitious platform)
commit: fix(phase3): P27F1 — empty-text community notices reach presence routes; no fictitious clip platform

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the gate report
`docs/campaigns/phase3/scaffolding/gate-report-1.md` (read F1 and F4 in full — they carry runnable
reproductions), the phase 3 spec (R1, R2) and plan (P5/P7/P9/P18/P21/P22).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (project virtualenv).

## F1 — Major: ordinary empty-text community notices never reach presence routes

Locations: `modules/brain/__init__.py:4849`; producers `modules/twitch/__init__.py:2646`,
`modules/kick/__init__.py:822`, `modules/youtube/__init__.py:825`.

The adapters legitimately produce an EMPTY text for Twitch follows, Kick notices and YouTube new members/gifts,
but `_message_of_event` requires non-blank text before it examines the event kind, so those notices are
rejected, admitted work ends `malformed_work`, and the presence routes never fire — which is precisely what the
presence pack exists to do. Fix the production behaviour so a notice carrying a **kind** is accepted with an
empty text, while a genuine TEXT event with blank text stays malformed. Turn the gate's reproduction into a
running test (all three platforms, plus the nonempty controls).

## F4 — Minor: scheduler service becomes a fictitious clip platform

Locations: `modules/clips/__init__.py:424`, `:430`; `tests/test_presence_pack.py:559`, `:567`.

The clips module accepts a service whose platform is a scheduler, producing a fictitious clip platform, and the
presence test accepts that state. A clip service must only be accepted from a platform that actually provides
clips (per the spec's honest-capability rule); an unsupported platform must leave the action unbound with a
named reason. Fix both the module and the test's expectation.

## Constraints
- Fix the PRODUCTION code; never weaken, skip or delete an unrelated assertion. Where an assertion encoded the
  defect, change it to the normative outcome and say so in the commit body.
- Keep the phase 0–2 guarantees (malformed work stays rejected and terminal; default-deny; bounded).
- One atomic commit; message pinned above.
- Report: `file:line` per finding, the test that now enforces each, anything not fixed and why.
