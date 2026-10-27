# P12F4 — Firefox cannot use the UI at all, and the operator trial found three more defects

The operator's browser trial (AC15) finally happened, and it found what no test had: **the configuration UI
refuses every state-changing request from Firefox**. Reproduce it with the operator's own request before
changing anything.

## B5 (blocker) — `Origin: null` is refused, so Firefox cannot submit any form

The operator's request (Firefox 158, private window, fresh session) carried:

    POST /language
    Origin: null
    Cookie: config_ui_session=…
    csrf_token=<a valid token for that session>&language=fr
    -> 403 Forbidden

Measured on the running container, same session, same token:

    Origin: null                     -> 403
    (no Origin header)               -> 303
    Origin: http://127.0.0.1:8765    -> 303

The cause is ours: `core/config_ui/__init__.py:3086` sends `Referrer-Policy: no-referrer` on every response,
and Firefox serialises the origin of a same-origin form POST as the literal `null` under that policy. The
guard then treats `null` as a foreign origin. **Every POST — wizard screens, language, console actions — is
refused in Firefox**, while Chrome and curl work.

Fix it at the root and keep the protection:
- stop forcing `no-referrer`: send `same-origin`, which still keeps the access URL (and its token) out of any
  referrer sent to another host;
- accept the literal `null` origin **only** when the CSRF token validates for the session, and refuse it
  otherwise. The CSRF token is the primary defence — an attacker's page cannot read it — and the cookie stays
  `HttpOnly; SameSite=Strict`; the origin check is depth, not the wall;
- a foreign origin (`http://evil.example`, `https://127.0.0.1:8765`, a different port) stays refused even
  with a valid token: assert that case, it is the one that must never regress.

## B6 (blocker) — a CSRF mismatch has no recovery path

The operator hit a stale page (session rotated under it) and got a bare `403 Forbidden` with no way back: no
fresh token, no message, no link. A streamer cannot recover without clearing cookies. When a POST's CSRF does
not match, re-render the same screen with a **fresh** token and a plain-language message ("cette page a
expiré ; vérifiez le champ et renvoyez"), keeping the submitted values where it is safe to do so. Keep the
origin refusal a bare 403 — that one is an attack, not a user.

## M5 (major) — the channel-points profile renders an empty screen 1

Choosing `channel_points` shows a screen with nothing on it and no explanation. The profile genuinely has no
parameter of its own on that screen; say so, in the same place: "ce profil n'a rien à configurer ici ; les
réglages viennent de l'écran suivant". An empty page reads as a bug, and it is one.

## M6 (minor) — the favicon 404s

Every page load asks for `/favicon.ico` and gets a 404, which fills a browser's console and a proxy's log.
Serve a tiny icon, or answer `204 No Content` on that path.

## What the operator's trial still needs

The Twitch screen demands `client_id`, `client_secret`, `access_token` and the numeric channel id, which a
non-technical streamer cannot produce. **Record that as a limit of the wizard as it stands** (the guide must
say where those come from, in steps a streamer can follow) — do not invent a credential flow in this step. The
operator's session continues after these fixes: five screens, the restart, the console.

## Acceptance

- The operator's exact request now answers 303 (with a valid token) and 403 (with an invalid one, or a foreign
  origin) — paste both.
- A POST with a stale token re-renders with a fresh one and a message, not a bare 403.
- The favicon path answers without a 404.
- The five trial nodes still pass, twice; the ordinary suite is green with no daemon; no new exemption outside
  A2's single class; one atomic commit per concern.
